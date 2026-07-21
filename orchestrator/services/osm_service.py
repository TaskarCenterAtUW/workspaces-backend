import base64
import http.client as httplib
import sys
import urllib.parse as urlparse
import xml.etree.cElementTree as ElementTree

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

    def request(self, conn, method, url, body, headers, progress):
        if progress:
            conn.putrequest(method, url)
            if body:
                conn.putheader("Content-Length", str(len(body)))
            for hdr, value in headers.items():
                conn.putheader(hdr, value)
            conn.endheaders()
            if body:
                start = 0
                size = len(body)
                chunk = size / 100
                if chunk < 16384:
                    chunk = 16384
                while start < size:
                    end = min(size, int(start + chunk))
                    conn.send(body[start:end])
                    start = end
        else:
            conn.request(method, url, body, headers)

    def _run_request(self, method, url, body=None, progress=0, content_type="text/xml"):
        url = urlparse.urljoin(self.url, url)
        purl = urlparse.urlparse(url)
        if purl.scheme not in ["https", "http"]:
            raise ValueError("Unsupported url scheme: %r" % (purl.scheme,))
        if ":" in purl.netloc:
            host, port = purl.netloc.split(":", 1)
            port = int(port)
        else:
            host = purl.netloc
            port = None
        url = purl.path
        if purl.query:
            url += "?" + purl.query
        headers = {}
        if body:
            headers["Content-Type"] = content_type

        try_no_auth = 0

        if not try_no_auth and not self.auth_token:
            raise HTTPError(0, "Need an auth token")

        try:
            if purl.scheme == "https":
                conn = httplib.HTTPSConnection(host, port)
            else:
                conn = httplib.HTTPConnection(host, port)
            #            conn.set_debuglevel(10)

            if try_no_auth:
                self.logger.info("Trying request without auth")
                self.request(conn, method, url, body, headers, progress)
                sys.stderr.flush()
                response = conn.getresponse()

            if not try_no_auth or (
                response.status == httplib.UNAUTHORIZED and self.auth_token
            ):
                if try_no_auth:
                    conn.close()
                    self.logger.info("re-connecting")
                    if purl.scheme == "https":
                        conn = httplib.HTTPSConnection(host, port)
                    else:
                        conn = httplib.HTTPConnection(host, port)

                creds = self.auth_token
                # headers["Authorization"] = "Basic " + base64.b64encode(
                #     bytes(creds, "utf8")
                # ).decode("utf8")
                headers["Authorization"] = "Bearer " + creds
                headers['X-Workspace'] = self.workspace_id
                self.logger.info("Trying request with auth")
                self.request(conn, method, url, body, headers, progress)
                response = conn.getresponse()

            if response.status == httplib.OK:
                response_body = response.read()
            else:
                err = response.read()
                raise HTTPError(
                    response.status,
                    "%03i: %s (%s)" % (response.status, response.reason, err),
                    err,
                )
        finally:
            conn.close()
        return response_body

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

    def upload(self, change):
        if self.changeset is None:
            raise RuntimeError("Changeset not opened")
        self.logger.info("Sending changes")
        for operation in change:
            if operation.tag not in ("create", "modify", "delete"):
                continue
            for element in operation:
                element.attrib["changeset"] = str(self.changeset)
        body = ElementTree.tostring(change, "utf-8")
        reply = self._run_request(
            "POST", "/api/0.6/changeset/%i/upload" % (self.changeset,), body, 1
        )
        self.logger.info("done.")
        return reply

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
        reply = self._run_request(
            "PUT", "/api/0.6/changeset/%i/close" % (self.changeset,)
        )
        self.changeset = None
        self.logger.info("done.")
