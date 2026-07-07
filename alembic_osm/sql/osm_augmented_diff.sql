-- osm_augmented_diff(p_changeset_id bigint) -> table of adiff rows
--
-- Computes a complete augmented diff for a changeset like those produced by
-- the Overpass API:
--
--   https://wiki.openstreetmap.org/wiki/Overpass_API/Augmented_Diffs
--
-- Each returned row represents one action. Callers convert rows to whatever
-- output format they need (JSON, XML, etc.).
--

CREATE OR REPLACE FUNCTION osm_augmented_diff(p_changeset_id bigint)
RETURNS TABLE (
  action_type       text,               -- 'create' | 'modify' | 'delete'
  element_type      text,               -- 'node' | 'way' | 'relation'
  -- new element (always populated)
  new_id            bigint,
  new_version       bigint,
  new_changeset_id  bigint,
  new_timestamp     timestamp without time zone,
  new_visible       boolean,
  new_user          text,
  new_uid           bigint,
  new_lat           double precision,   -- nodes only
  new_lon           double precision,   -- nodes only
  new_tags          jsonb,
  new_nodes         jsonb,              -- ways only:      [{ref,lat,lon}]
  new_members       jsonb,              -- relations only: [{type,ref,role}]
  -- old element (all NULL for create actions)
  old_id            bigint,
  old_version       bigint,
  old_changeset_id  bigint,
  old_timestamp     timestamp without time zone,
  old_visible       boolean,
  old_user          text,
  old_uid           bigint,
  old_lat           double precision,
  old_lon           double precision,
  old_tags          jsonb,
  old_nodes         jsonb,
  old_members       jsonb
)
LANGUAGE sql
STABLE
AS $$
WITH

-- Nodes in this changeset
cs_node_base AS (
  SELECT n.node_id                 AS id,
         n.version,
         n.changeset_id,
         n.timestamp,
         n.visible,
         n.latitude  / 10000000.0  AS lat,
         n.longitude / 10000000.0  AS lon,
         CASE
           WHEN n.version = 1 THEN 'create'
           WHEN NOT n.visible THEN 'delete'
           ELSE                    'modify'
         END                       AS action
    FROM nodes n
   WHERE n.changeset_id = p_changeset_id
     AND n.redaction_id IS NULL
),

cs_nodes AS (
  SELECT b.*,
         COALESCE(
           (
             SELECT jsonb_object_agg(k, v)
               FROM node_tags t
              WHERE t.node_id = b.id
                AND t.version = b.version
           ),
           '{}'::jsonb
         ) AS tags,
         usr.uid,
         usr.username
    FROM cs_node_base b
    LEFT JOIN LATERAL (
     SELECT u.id           AS uid,
            u.display_name AS username
       FROM changesets c
       JOIN users u
         ON u.id = c.user_id
      WHERE c.id = b.changeset_id
      LIMIT 1
   ) usr ON true
),

-- Previous node versions (for modify/delete)
prev_nodes AS (
  SELECT n.node_id                 AS id,
         n.version,
         n.changeset_id,
         n.timestamp,
         n.visible,
         n.latitude  / 10000000.0  AS lat,
         n.longitude / 10000000.0  AS lon,
         COALESCE(
           (
             SELECT jsonb_object_agg(k, v)
               FROM node_tags t
              WHERE t.node_id = n.node_id
                AND t.version = n.version
           ),
           '{}'::jsonb
         ) AS tags,
         usr.uid,
         usr.username
    FROM cs_nodes csn
    JOIN nodes n
      ON n.node_id = csn.id
     AND n.version = csn.version - 1
    LEFT JOIN LATERAL (
     SELECT u.id           AS uid,
            u.display_name AS username
       FROM changesets c
       JOIN users u
         ON u.id = c.user_id
      WHERE c.id = n.changeset_id
      LIMIT 1
   ) usr ON true
   WHERE csn.version > 1
     AND n.redaction_id IS NULL
),

-- Ways in this changeset
cs_way_base AS (
  SELECT w.way_id             AS id,
         w.version,
         w.changeset_id,
         w.timestamp,
         w.visible,
         CASE
           WHEN w.version = 1 THEN 'create'
           WHEN NOT w.visible THEN 'delete'
           ELSE                    'modify'
         END                  AS action
    FROM ways w
   WHERE w.changeset_id = p_changeset_id
     AND w.redaction_id IS NULL
),

-- Resolve each node ref to its coords as of this changeset (latest version ≤ $1)
cs_way_nodes AS (
  SELECT wn.way_id,
         wn.version,
         jsonb_agg(
           jsonb_build_object('ref', wn.node_id, 'lat', nc.lat, 'lon', nc.lon)
           ORDER BY wn.sequence_id
         ) AS nodes
    FROM way_nodes wn
    JOIN cs_way_base csw
      ON csw.id = wn.way_id
     AND csw.version = wn.version
    LEFT JOIN LATERAL (
     SELECT n.latitude / 10000000.0  AS lat,
            n.longitude / 10000000.0 AS lon
       FROM nodes n
      WHERE n.node_id = wn.node_id
        AND n.changeset_id <= p_changeset_id
        AND n.redaction_id IS NULL
      ORDER BY n.version DESC
      LIMIT 1
   ) nc ON true
   GROUP BY wn.way_id, wn.version
),

cs_ways AS (
  SELECT b.*,
         COALESCE(
           (
             SELECT jsonb_object_agg(k, v)
               FROM way_tags t
              WHERE t.way_id = b.id
                AND t.version = b.version
           ),
           '{}'::jsonb
         ) AS tags,
         COALESCE(wn.nodes, '[]'::jsonb) AS nodes,
         usr.uid,
         usr.username
    FROM cs_way_base b
    LEFT JOIN cs_way_nodes wn
      ON wn.way_id = b.id
     AND wn.version = b.version
    LEFT JOIN LATERAL (
     SELECT u.id           AS uid,
            u.display_name AS username
       FROM changesets c
       JOIN users u
         ON u.id = c.user_id
      WHERE c.id = b.changeset_id
      LIMIT 1
   ) usr ON true
),

-- Previous way versions (for modify/delete)
prev_way_base AS (
  SELECT w.way_id AS id,
         w.version,
         w.changeset_id,
         w.timestamp,
         w.visible
    FROM cs_ways csw
    JOIN ways w
      ON w.way_id = csw.id
     AND w.version = csw.version - 1
   WHERE csw.version > 1
     AND w.redaction_id IS NULL
),

-- Use changeset_id < p_changeset_id (strictly before) so coords reflect the
-- state before any node moves in this changeset
prev_way_nodes AS (
  SELECT wn.way_id,
         wn.version,
         jsonb_agg(
           jsonb_build_object('ref', wn.node_id, 'lat', nc.lat, 'lon', nc.lon)
           ORDER BY wn.sequence_id
         ) AS nodes
    FROM way_nodes wn
    JOIN prev_way_base pw
      ON pw.id = wn.way_id
     AND pw.version = wn.version
    LEFT JOIN LATERAL (
     SELECT n.latitude / 10000000.0  AS lat,
            n.longitude / 10000000.0 AS lon
       FROM nodes n
      WHERE n.node_id = wn.node_id
        AND n.changeset_id < p_changeset_id
        AND n.redaction_id IS NULL
      ORDER BY n.version DESC
      LIMIT 1
   ) nc ON true
   GROUP BY wn.way_id, wn.version
),

prev_ways AS (
  SELECT b.*,
         COALESCE(
           (
             SELECT jsonb_object_agg(k, v)
               FROM way_tags t
              WHERE t.way_id = b.id
                AND t.version = b.version
           ),
           '{}'::jsonb
         ) AS tags,
         COALESCE(wn.nodes, '[]'::jsonb) AS nodes,
         usr.uid,
         usr.username
    FROM prev_way_base b
    LEFT JOIN prev_way_nodes wn
      ON wn.way_id = b.id
     AND wn.version = b.version
    LEFT JOIN LATERAL (
     SELECT u.id           AS uid,
            u.display_name AS username
       FROM changesets c
       JOIN users u
         ON u.id = c.user_id
      WHERE c.id = b.changeset_id
      LIMIT 1
   ) usr ON true
),

-- Relations in this changeset
cs_rel_base AS (
  SELECT r.relation_id           AS id,
         r.version,
         r.changeset_id,
         r.timestamp,
         r.visible,
         CASE
           WHEN r.version = 1 THEN 'create'
           WHEN NOT r.visible THEN 'delete'
           ELSE                    'modify'
         END                     AS action
    FROM relations r
   WHERE r.changeset_id = p_changeset_id
     AND r.redaction_id IS NULL
),

cs_rel_members AS (
  SELECT rm.relation_id AS id,
         rm.version,
         jsonb_agg(
           jsonb_build_object(
             'type', lower(rm.member_type::text),
             'ref',  rm.member_id,
             'role', rm.member_role
           )
           ORDER BY rm.sequence_id
         ) AS members
    FROM relation_members rm
    JOIN cs_rel_base cr
      ON cr.id = rm.relation_id
     AND cr.version = rm.version
   GROUP BY rm.relation_id, rm.version
),

cs_relations AS (
  SELECT b.*,
         COALESCE(
           (
             SELECT jsonb_object_agg(k, v)
               FROM relation_tags t
              WHERE t.relation_id = b.id
                AND t.version = b.version
           ),
           '{}'::jsonb
         ) AS tags,
         COALESCE(m.members, '[]'::jsonb) AS members,
         usr.uid,
         usr.username
    FROM cs_rel_base b
    LEFT JOIN cs_rel_members m
      ON m.id = b.id
     AND m.version = b.version
    LEFT JOIN LATERAL (
     SELECT u.id           AS uid,
            u.display_name AS username
       FROM changesets c
       JOIN users u
         ON u.id = c.user_id
      WHERE c.id = b.changeset_id
      LIMIT 1
   ) usr ON true
),

-- Previous relation versions (for modify/delete)
prev_rel_base AS (
  SELECT r.relation_id AS id,
         r.version,
         r.changeset_id,
         r.timestamp,
         r.visible
    FROM cs_relations csr
    JOIN relations r
      ON r.relation_id = csr.id
     AND r.version = csr.version - 1
   WHERE csr.version > 1
     AND r.redaction_id IS NULL
),

prev_rel_members AS (
  SELECT rm.relation_id AS id,
         rm.version,
         jsonb_agg(
           jsonb_build_object(
             'type', lower(rm.member_type::text),
             'ref',  rm.member_id,
             'role', rm.member_role
           )
           ORDER BY rm.sequence_id
         ) AS members
    FROM relation_members rm
    JOIN prev_rel_base pr
      ON pr.id = rm.relation_id
     AND pr.version = rm.version
   GROUP BY rm.relation_id, rm.version
),

prev_relations AS (
  SELECT b.*,
         COALESCE(
           (
             SELECT jsonb_object_agg(k, v)
               FROM relation_tags t
              WHERE t.relation_id = b.id
                AND t.version = b.version
           ),
           '{}'::jsonb
         ) AS tags,
         COALESCE(m.members, '[]'::jsonb) AS members,
         usr.uid,
         usr.username
    FROM prev_rel_base b
    LEFT JOIN prev_rel_members m
      ON m.id = b.id
     AND m.version = b.version
    LEFT JOIN LATERAL (
     SELECT u.id           AS uid,
            u.display_name AS username
       FROM changesets c
       JOIN users u
         ON u.id = c.user_id
      WHERE c.id = b.changeset_id
      LIMIT 1
   ) usr ON true
),

-- Ways affected by node geometry changes:
--
-- When a node moves, every way containing it has an implicit geometry change
-- even if the way record was untouched. We emit a 'modify' row for each such
-- way so the diff viewer can render the shape change.
-- Ways already explicitly in this changeset are excluded.
affected_way_ids AS (
  SELECT DISTINCT cwn.way_id AS id
    FROM current_way_nodes cwn
   WHERE cwn.node_id IN (
           SELECT id
             FROM cs_nodes
            WHERE action IN ('modify', 'delete')
         )
     AND cwn.way_id NOT IN (
           SELECT id
             FROM cs_way_base
         )
),

-- Resolve each affected way to the version it was at when this changeset ran
affected_way_versions AS (
  SELECT awi.id,
         w.version,
         w.changeset_id,
         w.timestamp,
         w.visible
    FROM affected_way_ids awi
    JOIN LATERAL (
     SELECT w2.version,
            w2.changeset_id,
            w2.timestamp,
            w2.visible
       FROM ways w2
      WHERE w2.way_id = awi.id
        AND w2.changeset_id <= p_changeset_id
        AND w2.redaction_id IS NULL
      ORDER BY w2.version DESC
      LIMIT 1
   ) w ON true
),

-- "new" node coords: after this changeset's node edits (<=)
affected_nodes_new AS (
  SELECT wn.way_id,
         wn.version,
         jsonb_agg(
           jsonb_build_object('ref', wn.node_id, 'lat', nc.lat, 'lon', nc.lon)
           ORDER BY wn.sequence_id
         ) AS nodes
    FROM way_nodes wn
    JOIN affected_way_versions aw
      ON aw.id = wn.way_id
     AND aw.version = wn.version
    LEFT JOIN LATERAL (
     SELECT n.latitude / 10000000.0  AS lat,
            n.longitude / 10000000.0 AS lon
       FROM nodes n
      WHERE n.node_id = wn.node_id
        AND n.changeset_id <= p_changeset_id
        AND n.redaction_id IS NULL
      ORDER BY n.version DESC
      LIMIT 1
   ) nc ON true
   GROUP BY wn.way_id, wn.version
),

-- "old" node coords: before this changeset's node edits (<)
affected_nodes_old AS (
  SELECT wn.way_id,
         wn.version,
         jsonb_agg(
           jsonb_build_object('ref', wn.node_id, 'lat', nc.lat, 'lon', nc.lon)
           ORDER BY wn.sequence_id
         ) AS nodes
    FROM way_nodes wn
    JOIN affected_way_versions aw
      ON aw.id = wn.way_id
     AND aw.version = wn.version
    LEFT JOIN LATERAL (
     SELECT n.latitude / 10000000.0  AS lat,
            n.longitude / 10000000.0 AS lon
       FROM nodes n
      WHERE n.node_id = wn.node_id
        AND n.changeset_id < p_changeset_id
        AND n.redaction_id IS NULL
      ORDER BY n.version DESC
      LIMIT 1
   ) nc ON true
   GROUP BY wn.way_id, wn.version
),

affected_ways AS (
  SELECT aw.*,
         COALESCE(
           (
             SELECT jsonb_object_agg(k, v)
               FROM way_tags t
              WHERE t.way_id = aw.id
                AND t.version = aw.version
           ),
           '{}'::jsonb
         ) AS tags,
         COALESCE(anew.nodes, '[]'::jsonb) AS nodes_new,
         COALESCE(aold.nodes, '[]'::jsonb) AS nodes_old,
         usr.uid,
         usr.username
    FROM affected_way_versions aw
    LEFT JOIN affected_nodes_new  anew
      ON anew.way_id = aw.id
     AND anew.version = aw.version
    LEFT JOIN affected_nodes_old  aold
      ON aold.way_id = aw.id
     AND aold.version = aw.version
    LEFT JOIN LATERAL (
     SELECT u.id           AS uid,
            u.display_name AS username
       FROM changesets c
       JOIN users u
         ON u.id = c.user_id
      WHERE c.id = aw.changeset_id
      LIMIT 1
   ) usr ON true
)

-- Emit one row per action
SELECT csn.action,
       'node',
       csn.id,
       csn.version,
       csn.changeset_id,
       csn.timestamp,
       csn.visible,
       csn.username,
       csn.uid,
       csn.lat,
       csn.lon,
       csn.tags,
       NULL::jsonb,
       NULL::jsonb,
       pn.id,
       pn.version,
       pn.changeset_id,
       pn.timestamp,
       pn.visible,
       pn.username,
       pn.uid,
       pn.lat,
       pn.lon,
       pn.tags,
       NULL::jsonb,
       NULL::jsonb
  FROM cs_nodes csn
  LEFT JOIN prev_nodes pn
    ON pn.id = csn.id
   AND pn.version = csn.version - 1

UNION ALL

SELECT csw.action,
       'way',
       csw.id,
       csw.version,
       csw.changeset_id,
       csw.timestamp,
       csw.visible,
       csw.username,
       csw.uid,
       NULL::double precision,
       NULL::double precision,
       csw.tags,
       csw.nodes,
       NULL::jsonb,
       pw.id,
       pw.version,
       pw.changeset_id,
       pw.timestamp,
       pw.visible,
       pw.username,
       pw.uid,
       NULL::double precision,
       NULL::double precision,
       pw.tags,
       pw.nodes,
       NULL::jsonb
  FROM cs_ways csw
  LEFT JOIN prev_ways pw
    ON pw.id = csw.id
   AND pw.version = csw.version - 1

UNION ALL

SELECT csr.action,
       'relation',
       csr.id,
       csr.version,
       csr.changeset_id,
       csr.timestamp,
       csr.visible,
       csr.username,
       csr.uid,
       NULL::double precision,
       NULL::double precision,
       csr.tags,
       NULL::jsonb,
       csr.members,
       pr.id,
       pr.version,
       pr.changeset_id,
       pr.timestamp,
       pr.visible,
       pr.username,
       pr.uid,
       NULL::double precision,
       NULL::double precision,
       pr.tags,
       NULL::jsonb,
       pr.members
  FROM cs_relations csr
  LEFT JOIN prev_relations pr
    ON pr.id = csr.id
   AND pr.version = csr.version - 1

UNION ALL

-- Implicit way modifications from node moves: new and old share way metadata;
-- only the node coordinate arrays differ.
SELECT 'modify',
       'way',
       aw.id,
       aw.version,
       aw.changeset_id,
       aw.timestamp,
       aw.visible,
       aw.username,
       aw.uid,
       NULL::double precision,
       NULL::double precision,
       aw.tags,
       aw.nodes_new,
       NULL::jsonb,
       aw.id,
       aw.version,
       aw.changeset_id,
       aw.timestamp,
       aw.visible,
       aw.username,
       aw.uid,
       NULL::double precision,
       NULL::double precision,
       aw.tags,
       aw.nodes_old,
       NULL::jsonb
  FROM affected_ways aw;
$$;
