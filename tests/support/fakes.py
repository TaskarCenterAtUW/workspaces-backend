"""Fake async DB session for integration tests.

The application talks to its databases exclusively through SQLAlchemy /
SQLModel ``AsyncSession`` objects (the "data fetcher"). Repositories,
routes, schemas and serialization all sit *above* that boundary.

``FakeSession`` stands in for a real session: instead of running SQL it
returns pre-programmed ``FakeResult`` objects from a FIFO queue. This lets
a test drive a real HTTP request through the real repository code while
feeding it simulated rows -- no Postgres, PostGIS or Docker required.

Because the boundary is the session, a test must queue results in the
ORDER the repository issues queries. Each repository method documents its
query sequence; the integration tests show the common patterns. The most
common helpers are :func:`rows` (a SELECT result) and :func:`affected`
(an UPDATE/DELETE rowcount).
"""

from collections import deque
from itertools import count

from sqlalchemy.exc import NoResultFound


class _Scalars:
    """Mimics the object returned by ``result.scalars()``."""

    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _Mappings:
    """Mimics the object returned by ``result.mappings()`` (raw-SQL dict rows)."""

    def __init__(self, mappings):
        self._mappings = list(mappings)

    def all(self):
        return list(self._mappings)

    def first(self):
        return self._mappings[0] if self._mappings else None

    def __iter__(self):
        return iter(self._mappings)


class FakeResult:
    """A canned result returned by :meth:`FakeSession.execute` / ``exec``.

    ``rows``      -- ORM entities for ``scalars()`` / ``scalar_one_or_none()``.
    ``mappings``  -- dict rows for ``mappings()`` (used by raw-SQL queries).
    ``rowcount``  -- value returned by ``result.rowcount`` (UPDATE/DELETE).
    ``value``     -- value returned by ``session.scalar(...)``.
    """

    def __init__(self, rows=None, mappings=None, rowcount=1, value=None):
        self._rows = list(rows) if rows is not None else []
        self._mappings = list(mappings) if mappings is not None else []
        self.rowcount = rowcount
        self.value = value

    def scalars(self):
        return _Scalars(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar_one(self):
        if len(self._rows) != 1:
            raise NoResultFound("FakeResult.scalar_one() expected exactly one row")
        return self._rows[0]

    def mappings(self):
        return _Mappings(self._mappings)

    def all(self):
        # Used by queries that select multiple columns (rows are tuples).
        return list(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    """Drop-in async stand-in for a SQLModel ``AsyncSession``.

    Queue results with :meth:`queue` (or pass them to the constructor); each
    ``execute`` / ``exec`` call pops the next one. ``commit`` / ``add`` /
    ``rollback`` / ``refresh`` are recorded so tests can assert on writes.
    """

    def __init__(self, *responses):
        self._responses = deque(responses)
        self.added = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self._id_seq = count(1)

    def queue(self, *responses):
        """Append results to the response queue. Returns self for chaining."""
        self._responses.extend(responses)
        return self

    def _next(self):
        return self._responses.popleft() if self._responses else FakeResult(rows=[])

    @staticmethod
    def _is_session_setup(statement):
        # Raw ``SET search_path ...`` statements (used by the OSM bbox query)
        # carry no result and should not consume a queued response.
        try:
            return str(statement).strip().upper().startswith("SET ")
        except Exception:
            return False

    async def execute(self, statement, *args, **kwargs):
        if self._is_session_setup(statement):
            return FakeResult(rows=[])
        return self._next()

    async def exec(self, statement, *args, **kwargs):
        return self._next()

    async def scalar(self, statement, *args, **kwargs):
        result = self._next()
        return result.value if isinstance(result, FakeResult) else result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def refresh(self, obj, *args, **kwargs):
        # Simulate the DB assigning an autoincrement primary key on insert.
        if getattr(obj, "id", "missing") is None:
            obj.id = next(self._id_seq)

    async def close(self):
        self.closed = True


# --- result builders -------------------------------------------------------


def rows(*entities, rowcount=None):
    """A SELECT result yielding ``entities`` for ``scalars()`` / ``scalar_one*``."""
    return FakeResult(
        rows=list(entities),
        rowcount=len(entities) if rowcount is None else rowcount,
    )


def empty():
    """A SELECT result with no rows (drives NotFound paths)."""
    return FakeResult(rows=[], rowcount=0)


def affected(n):
    """An UPDATE/DELETE result reporting ``n`` affected rows."""
    return FakeResult(rows=[], rowcount=n)


def mappings(*dict_rows):
    """A raw-SQL result yielding dict rows for ``result.mappings()``."""
    return FakeResult(mappings=list(dict_rows))


def scalar(value):
    """A result for ``session.scalar(...)`` (e.g. an EXISTS check)."""
    return FakeResult(value=value)
