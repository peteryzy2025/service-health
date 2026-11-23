import logging
from time import perf_counter

log = logging.getLogger("monitor.requests")


class RequestTimer:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = perf_counter()
        response = self.get_response(request)
        elapsed = (perf_counter() - start) * 1000
        response["X-Request-Duration-Ms"] = f"{elapsed:.2f}"
        log.info("%s %s %s %.2fms", request.method, request.path, response.status_code, elapsed)
        return response
