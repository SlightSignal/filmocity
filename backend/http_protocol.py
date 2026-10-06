"""Keep completed Windows reads from re-entering a closed HTTP connection."""
import h11
from uvicorn.protocols.http.h11_impl import H11Protocol


class FilmocityH11Protocol(H11Protocol):
    def data_received(self, data):
        # Proactor can deliver a completed read from its finally block after
        # transport.close(). H11 has already sent ConnectionClosed by then;
        # parsing those bytes would attempt an illegal 400 response in CLOSED.
        # Keep active requests, keepalive and upgrade handling upstream.
        if self.transport.is_closing() or self.conn.our_state is h11.CLOSED:
            return
        super().data_received(data)
