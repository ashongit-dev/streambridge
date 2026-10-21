import json
import threading
import uuid
import websocket

class OBSRequestError(RuntimeError):
    """A safe diagnostic containing the request name/code, never its arguments."""
    pass

class OBS:
    def __init__(self):
        self.ws = None
        self.lock = threading.RLock()

    def connect(self):
        with self.lock:
            self.close()
            self.ws = websocket.create_connection("ws://127.0.0.1:4455", timeout=5)
            hello = json.loads(self.ws.recv())
            if hello.get("op") != 0 or hello["d"].get("authentication"):
                self.close()
                raise RuntimeError("Unexpected OBS handshake")
            self.ws.send(json.dumps({"op": 1, "d": {"rpcVersion": 1, "eventSubscriptions": 0}}))
            if json.loads(self.ws.recv()).get("op") != 2:
                raise RuntimeError("OBS identification failed")

    def close(self):
        if self.ws:
            self.ws.close()
        self.ws = None

    def call(self, kind, **data):
        with self.lock:
            if not self.ws:
                raise RuntimeError("Encoder is connecting")
            request_id = str(uuid.uuid4())
            self.ws.send(json.dumps({"op": 6, "d": {"requestType": kind, "requestId": request_id, "requestData": data}}))
            while True:
                reply = json.loads(self.ws.recv())
                if reply.get("op") != 7 or reply["d"].get("requestId") != request_id:
                    continue
                result = reply["d"]
                if not result["requestStatus"]["result"]:
                    # Do not expose OBS error strings that might include a stream key.
                    code = result["requestStatus"].get("code", "unknown")
                    raise OBSRequestError(f"Encoder rejected {kind} (code {code})")
                return result.get("responseData", {})
