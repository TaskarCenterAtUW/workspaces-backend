from collections.abc import Iterable
from contextlib import suppress
import xml.etree.cElementTree as ElementTree

import httpx
from lxml import etree

# from services.config import Config
from logging import getLogger


class HTTPError(Exception):
    pass


class OSMService(object):
    url = ""

    def __init__(self, auth_token: str, base_url: str, workspace_id: str = None):
        self.url = base_url
        self.auth_token = auth_token
        self.workspace_id = workspace_id

        self.changeset = None
        self.progress_msg = None
        self.logger = getLogger(__name__)

    def __del__(self):
        # if self.changeset is not None:
        #    self.close_changeset()
        pass

    @staticmethod
    def _local_name(tag_or_elem) -> str:
        tag = tag_or_elem.tag if hasattr(tag_or_elem, "tag") else tag_or_elem
        if isinstance(tag, str) and "}" in tag:
            return tag.rsplit("}", 1)[-1]
        return tag

    def _headers(self, include_content_type: bool, content_type: str) -> dict:
        if not self.auth_token:
            raise HTTPError(0, "Need an auth token")

        headers = {"Authorization": f"Bearer {self.auth_token}"}
        if self.workspace_id is not None:
            headers["X-Workspace"] = str(self.workspace_id)
        if include_content_type:
            headers["Content-Type"] = content_type
        return headers

    def _run_request(
        self,
        method,
        url,
        body=None,
        progress=0,
        content_type="text/xml",
    ):
        del progress
        include_content_type = body is not None
        headers = self._headers(include_content_type, content_type)

        with httpx.Client(base_url=self.url, timeout=60.0) as client:
            response = client.request(method, url, headers=headers, content=body)

        if response.status_code in (httpx.codes.OK, httpx.codes.NO_CONTENT):
            return response.content

        raise HTTPError(
            response.status_code,
            f"{response.status_code:03d}: {response.reason_phrase} ({response.content!r})",
            response.content,
        )

    def _iter_changeset_upload_chunks(self, xml_file_path: str) -> Iterable[bytes]:
        if self.changeset is None:
            raise RuntimeError("Changeset not opened")

        yield b'<?xml version="1.0" encoding="UTF-8"?>\n'
        yield b"<osmChange version=\"0.6\" generator=\"Workspaces Orchestrator\">"

        current_operation = None
        parsed_any_element = False
        context = etree.iterparse(xml_file_path, events=("end",))

        try:
            for _, elem in context:
                local_name = self._local_name(elem)
                if local_name not in {"node", "way", "relation"}:
                    continue

                parsed_any_element = True
                operation = "create"
                parent = elem.getparent()
                while parent is not None:
                    parent_name = self._local_name(parent)
                    if parent_name in {"create", "modify", "delete"}:
                        operation = parent_name
                        break
                    parent = parent.getparent()

                if current_operation != operation:
                    if current_operation is not None:
                        yield f"</{current_operation}>".encode("utf-8")
                    yield f"<{operation}>".encode("utf-8")
                    current_operation = operation

                elem.set("changeset", str(self.changeset))
                yield etree.tostring(elem, encoding="utf-8")

                elem.clear()
                with suppress(AttributeError):
                    while elem.getprevious() is not None:
                        del elem.getparent()[0]

            if current_operation is not None:
                yield f"</{current_operation}>".encode("utf-8")
            elif not parsed_any_element:
                yield b"<create></create>"

            yield b"</osmChange>"
        finally:
            del context

    def create_changeset(self, created_by, comment, source, url):
        if self.changeset is not None:
            raise RuntimeError("Changeset already opened")
        self.logger.info("Creating changeset")
        root = ElementTree.Element("osm")
        tree = ElementTree.ElementTree(root)
        element = ElementTree.SubElement(root, "changeset")
        ElementTree.SubElement(element, "tag", {"k": "url", "v": url})
        ElementTree.SubElement(element, "tag", {"k": "import", "v": "yes"})
        ElementTree.SubElement(element, "tag", {"k": "created_by", "v": created_by})
        ElementTree.SubElement(element, "tag", {"k": "comment", "v": comment})
        ElementTree.SubElement(element, "tag", {"k": "source", "v": source})
        body = ElementTree.tostring(root, "utf-8")
        reply = self._run_request("PUT", "/api/0.6/changeset/create", body)
        changeset = int(reply.strip())
        self.logger.info("Changeset ID: %i" % (changeset))
        self.changeset = changeset
        return changeset

    def fetch_way(self, way_id):
        self.logger.info("Fetching way %i" % (way_id,))
        reply = self._run_request("GET", "/api/0.6/way/%i" % (way_id,))
        self.logger.info("done.")
        return reply

    def fetch_multiple_ways(self, way_ids: list):
        self.progress_msg = "Fetching multiple ways"
        # create a string from way_ids integer array comma separated

        query = ",".join(str(way_id) for way_id in way_ids)
        url = f"/api/0.6/ways?ways={query}"
        reply = self._run_request("GET", url)
        self.logger.info("done.")
        return reply

    def fetch_multiple_nodes(self, node_ids: list):
        self.progress_msg = "Fetching multiple nodes"
        query = ",".join(str(node_id) for node_id in node_ids)
        url = f"/api/0.6/nodes?nodes={query}"
        reply = self._run_request("GET", url)
        self.logger.info("done.")
        return reply

    def upload(self, xml_file_path: str):
        if self.changeset is None:
            raise RuntimeError("Changeset not opened")

        self.logger.info("Sending changes")

        headers = self._headers(include_content_type=True, content_type="text/xml")
        stream = self._iter_changeset_upload_chunks(xml_file_path)
        endpoint = f"/api/0.6/changeset/{self.changeset}/upload"

        with httpx.Client(base_url=self.url, timeout=None) as client:
            reply = client.request("POST", endpoint, headers=headers, content=stream)

        if reply.status_code not in (httpx.codes.OK, httpx.codes.NO_CONTENT):
            raise HTTPError(
                reply.status_code,
                f"{reply.status_code:03d}: {reply.reason_phrase} ({reply.content!r})",
                reply.content,
            )
        self.logger.info("done.")
        return reply.content

    def download_changeset(self, changeset_id):
        self.logger.info("Downloading changeset %i" % (changeset_id,))
        reply = self._run_request(
            "GET", "/api/0.6/changeset/%i/download" % (changeset_id,)
        )
        self.logger.info("done.")
        return reply

    def close_changeset(self):
        if self.changeset is None:
            raise RuntimeError("Changeset not opened")
        self.logger.info("Closing changeset")
        self._run_request(
            "PUT", "/api/0.6/changeset/%i/close" % (self.changeset,)
        )
        self.changeset = None
        self.logger.info("done.")

    def create_workspace(self, workspace_id: str):
        self.logger.info(f"Creating workspace {workspace_id}")
        # body = f"<workspace><id>{workspace_id}</id></workspace>"
        self._run_request("PUT", f"/api/0.6/workspaces/{workspace_id}", body=None)
        self.logger.info("done.")
        return None
