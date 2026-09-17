"""LOAD-TEST — Locust entrypoint (closed-model, 1 request/iteration).

Chạy:
    BASE_URL=... API_KEY=... AUTH_BEARER=... RUN_ID=... LOAD_SCENARIO=realistic \
    locust -f tests/load/locustfile.py --headless \
      --html "$PREFIX.html" --csv "$PREFIX" --csv-full-history

Một User class DUY NHẤT, dispatch theo scenario (mix search/market 65/35 + pagination 80/20, hoặc
ops-only /health+/metadata, hoặc market-only). Mỗi iteration gửi ĐÚNG 1 HTTP request → giữ pacing
và tỉ lệ endpoint. Phân loại lỗi, histogram success-only, auto-abort, exit code: xem tests/load/metrics.py.
"""
from __future__ import annotations

import os
import time
import uuid

import locust as _locust
from locust import HttpUser, constant_pacing, events, task
from requests import exceptions as rex

from tests.load import config as C
from tests.load.corpus import UserCorpus, corpus_hash
from tests.load.finalize import (
    _read_deployment,
    _read_published_batch,
    deployment_mismatches,
)
from tests.load.metrics import PHASE, MetricsCollector
from tests.load.preflight import check_pagination
from tests.load.response_validation import (
    base_error_class,
    enforce_duration_limit,
    valid_success_body,
)
from tests.load.shapes import ScenarioShape  # noqa: F401 — import để Locust đăng ký shape

# state toàn cục cho run
_CFG: C.RunConfig | None = None
_COLLECTOR: MetricsCollector | None = None
_user_counter = 0
_as_of_captured = False


def _capture_as_of(resp, ep: str) -> None:
    """Bắt data cutoff (as_of) từ response search/market ĐẦU TIÊN → metadata tái lập (nếu chưa có batch_id)."""
    global _as_of_captured
    if _as_of_captured or ep not in (C.EP_SEARCH, C.EP_MARKET, C.EP_METADATA):
        return  # /metadata cũng có as_of → fallback cho scenario ops_baseline
    try:
        as_of = (resp.json() or {}).get("as_of")
    except Exception:
        return
    if as_of:
        _CFG.extra["as_of"] = as_of
        _as_of_captured = True


@events.init.add_listener
def _on_init(environment, **_):
    global _CFG, _COLLECTOR
    _CFG = C.load_run_config()
    # Metadata tái lập ĐẦY ĐỦ (plan): version, revision/digest, batch, cache state, region, scaling.
    # Các trường hạ tầng do người vận hành truyền qua env (load generator không tự introspect Cloud Run).
    _CFG.extra.update({
        "base_url": _CFG.base_url,
        "corpus_hash": corpus_hash(),
        "locust_version": _locust.__version__,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cloud_run_revision": os.environ.get("PERF_REVISION", ""),
        "image_digest": os.environ.get("PERF_IMAGE_DIGEST", ""),
        "batch_id": os.environ.get("PERF_BATCH_ID", ""),
        "as_of": "",                                             # data cutoff, bắt từ response đầu tiên
        "cache_backend": os.environ.get("JOBS_API_CACHE_BACKEND", ""),
        "bq_use_query_cache": os.environ.get("JOBS_API_BQ_USE_QUERY_CACHE", ""),
        "bq_maximum_bytes_billed": os.environ.get("PERF_BQ_MAXIMUM_BYTES_BILLED", ""),
        "region": os.environ.get("PERF_REGION", ""),
        "service": os.environ.get("PERF_SVC", ""),
        "bq_project": os.environ.get("PERF_PROJECT", ""),
        "bq_dataset": os.environ.get("PERF_DATASET", ""),
        "bq_location": os.environ.get("PERF_BQ_LOCATION", ""),
        "query_timeout_s": os.environ.get("PERF_QUERY_TIMEOUT_S", ""),
        "cache_ttl_seconds": os.environ.get("PERF_CACHE_TTL_SECONDS", ""),
        "rate_limit_per_minute": os.environ.get("PERF_RATE_LIMIT_PER_MINUTE", ""),
        "rate_limit_burst": os.environ.get("PERF_RATE_LIMIT_BURST", ""),
        "run_concurrency": os.environ.get("RUN_CONCURRENCY", ""),
        "cpu_limit": os.environ.get("PERF_CPU_LIMIT", ""),
        "memory_limit": os.environ.get("PERF_MEMORY_LIMIT", ""),
        "min_instances": os.environ.get("PERF_MIN_INSTANCES", ""),
        "max_instances": os.environ.get("PERF_MAX_INSTANCES", ""),
        "service_min_instances": os.environ.get("PERF_SERVICE_MIN_INSTANCES", ""),
        "service_max_instances": os.environ.get("PERF_SERVICE_MAX_INSTANCES", ""),
        "scaling_mode": os.environ.get("PERF_SCALING_MODE", ""),
    })
    # FAIL-FAST cho scenario NGHIỆM THU (không phải smoke): thiếu provenance hạ tầng → report vô nghĩa.
    if _CFG.scenario != "smoke":
        need = ["cloud_run_revision", "image_digest", "batch_id",
                "cache_backend", "bq_use_query_cache",
                "bq_maximum_bytes_billed",
                "region", "service", "bq_project", "bq_dataset", "bq_location",
                "query_timeout_s", "cache_ttl_seconds",
                "rate_limit_per_minute", "rate_limit_burst",
                "run_concurrency", "cpu_limit", "memory_limit",
                "min_instances", "max_instances",
                "service_min_instances", "service_max_instances", "scaling_mode"]
        missing = [k for k in need if not _CFG.extra.get(k)]
        if missing:
            C.invalid_test(
                f"thiếu metadata hạ tầng {missing} cho scenario '{_CFG.scenario}'. "
                "Đặt env PERF_REVISION/PERF_IMAGE_DIGEST/PERF_BATCH_ID/"
                "JOBS_API_CACHE_BACKEND/JOBS_API_BQ_USE_QUERY_CACHE/"
                "PERF_REGION/PERF_SVC/PERF_PROJECT/PERF_DATASET/PERF_BQ_LOCATION/"
                "PERF_BQ_MAXIMUM_BYTES_BILLED/"
                "PERF_QUERY_TIMEOUT_S/PERF_CACHE_TTL_SECONDS/"
                "PERF_RATE_LIMIT_PER_MINUTE/PERF_RATE_LIMIT_BURST/"
                "RUN_CONCURRENCY/PERF_CPU_LIMIT/PERF_MEMORY_LIMIT/"
                "PERF_MIN_INSTANCES/PERF_MAX_INSTANCES/"
                "PERF_SERVICE_MIN_INSTANCES/PERF_SERVICE_MAX_INSTANCES/PERF_SCALING_MODE "
                "(xem RUNBOOK), "
                "hoặc dùng scenario 'smoke' để validate local.")
        try:
            concurrency = int(_CFG.extra["run_concurrency"])
            min_instances = int(_CFG.extra["min_instances"])
            max_instances = int(_CFG.extra["max_instances"])
            service_min_instances = int(_CFG.extra["service_min_instances"])
            service_max_raw = _CFG.extra["service_max_instances"].lower()
            service_max_instances = (
                None if service_max_raw == "default" else int(service_max_raw)
            )
            max_bytes = int(_CFG.extra["bq_maximum_bytes_billed"])
            query_timeout_s = int(_CFG.extra["query_timeout_s"])
            cache_ttl_s = int(_CFG.extra["cache_ttl_seconds"])
            rate_limit_per_minute = int(_CFG.extra["rate_limit_per_minute"])
            rate_limit_burst = int(_CFG.extra["rate_limit_burst"])
        except (TypeError, ValueError):
            C.invalid_test("RUN_CONCURRENCY/PERF_MIN_INSTANCES/PERF_MAX_INSTANCES/"
                           "PERF_SERVICE_MIN_INSTANCES/PERF_SERVICE_MAX_INSTANCES/"
                           "PERF_BQ_MAXIMUM_BYTES_BILLED/PERF_QUERY_TIMEOUT_S/"
                           "PERF_CACHE_TTL_SECONDS/PERF_RATE_LIMIT_* sai định dạng số/default")
        if (concurrency <= 0 or min_instances < 0 or max_instances <= 0
                or min_instances > max_instances or max_bytes <= 0
                or service_min_instances < 0
                or (service_max_instances is not None and service_max_instances <= 0)
                or query_timeout_s <= 0 or cache_ttl_s < 0
                or rate_limit_per_minute <= 0 or rate_limit_burst < 0):
            C.invalid_test("metadata hạ tầng dạng số không hợp lệ: cần concurrency/max/max_bytes > 0 "
                           "và 0 <= min_instances <= max_instances")
        if service_min_instances != 0 or service_max_instances is not None:
            C.invalid_test("Track B dùng revision-level scaling: service-level min/max phải là "
                           "0/default để không thay đổi tải hiệu lực")
        if _CFG.extra["scaling_mode"].lower() != "automatic":
            C.invalid_test("Track B cần Cloud Run automatic scaling; manual scaling bỏ qua "
                           "revision min/max và làm sai bài capacity")
        # Cache mode phải KHỚP scenario (không chỉ tồn tại) — nếu không, cold-query/cold-miss chạy nhầm.
        # LƯU Ý: đây là env do máy Locust khai; cache-hit ratio THỰC vẫn phải đối chiếu log/span server
        # sau run (README) — client không quan sát được bq.cache_hit trực tiếp.
        cache = _CFG.extra["cache_backend"].lower()
        bqc = _CFG.extra["bq_use_query_cache"].lower()
        cache_error = C.cache_mode_error(_CFG.scenario, cache, bqc)
        if cache_error:
            C.invalid_test(
                f"scenario '{_CFG.scenario}' cần {cache_error} — hiện cache_backend={cache!r}, "
                f"bq_use_query_cache={bqc!r} (cấu hình cache KHÔNG khớp scenario).")
        # Kiểm cấu hình THẬT trước khi phát tải có chi phí; finalizer vẫn kiểm lại sau run để chống
        # thay đổi giữa chừng. Sai revision/traffic/config phải dừng trước pre-flight pagination.
        deployment = _read_deployment(
            _CFG.extra["bq_project"], _CFG.extra["region"], _CFG.extra["service"],
            _CFG.extra["cloud_run_revision"],
        )
        if deployment is None:
            C.invalid_test("không đọc được service/revision Cloud Run trước run")
        mismatches = deployment_mismatches(_CFG.extra, deployment)
        if not deployment["traffic_100pct"] or mismatches:
            C.invalid_test(
                "Cloud Run pre-check không khớp: "
                f"traffic_100pct={deployment['traffic_100pct']}, mismatches={mismatches}")
        # PERF_BATCH_ID không được chỉ là provenance tự khai: đối chiếu pointer thật của đúng dataset
        # TRƯỚC khi phát tải có chi phí. Finalizer sẽ kiểm lại batch + as_of sau run để bắt thay đổi
        # snapshot giữa chừng.
        snapshot = _read_published_batch(
            _CFG.extra["bq_project"], _CFG.extra["bq_dataset"], _CFG.extra["bq_location"],
            max_bytes, _CFG.run_id,
        )
        if snapshot is None:
            C.invalid_test("không đọc được published batch của snapshot trước run")
        if snapshot["batch_id"] != _CFG.extra["batch_id"]:
            C.invalid_test(
                "PERF_BATCH_ID không khớp snapshot đang publish: "
                f"expected={_CFG.extra['batch_id']!r}, actual={snapshot['batch_id']!r}")
    _COLLECTOR = MetricsCollector(_CFG)
    environment.host = _CFG.base_url  # Locust không tự đọc BASE_URL → set host ở đây


@events.quitting.add_listener
def _on_quitting(environment, **_):
    if _COLLECTOR is None:
        return
    if PHASE.get() == "_safety_timeout":
        _COLLECTOR.trigger_abort("shape vượt planned duration + 120s (có thể không spawn/scale được user)")
    # +2s buffer: strftime làm TRÒN XUỐNG giây → nếu không đệm, log dưới-giây cuối (ts > ended_utc floored)
    _COLLECTOR.cfg.extra["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 2))
    rows, exit_code, notes = _COLLECTOR.evaluate()
    _COLLECTOR.write_reports(rows, exit_code, notes)
    environment.process_exit_code = exit_code
    print(f"\n[LOAD-TEST] exit_code={exit_code}")
    for n in notes:
        print(f"  - {n}")
    print(f"[LOAD-TEST] report: {_COLLECTOR.cfg.report_prefix}-success-metrics.json")


@events.test_start.add_listener
def _on_test_start(environment, **_):
    """Fail-fast nếu dataset không hỗ trợ pagination 80/20; tránh bắn cả run rồi mới INVALID."""
    if _CFG is None or _COLLECTOR is None or _CFG.scenario_def.weight_search <= 0:
        return
    headers = {}
    if _CFG.api_key:
        headers["X-API-Key"] = _CFG.api_key
    if _CFG.auth_bearer:
        headers["Authorization"] = f"Bearer {_CFG.auth_bearer}"
    started = time.perf_counter()
    ok, reason, body = check_pagination(_CFG.base_url, headers, _CFG.run_id)
    # Đây vẫn là HTTP request thật/BQ cost thật: đếm vào safety cap, nhưng gắn warmup nên không làm
    # bẩn percentile/pagination nghiệm thu.
    _COLLECTOR.record(
        C.EP_SEARCH, "warmup", (time.perf_counter() - started) * 1000,
        ok, None if ok else "preflight_invalid", None,
    )
    if not ok:
        _COLLECTOR.trigger_invalid(reason)
        environment.runner.quit()
        return
    _CFG.extra["pagination_preflight"] = {
        "pass": True,
        "payload": "search_corpus[0]",
    }
    if body and body.get("as_of") and not _CFG.extra.get("as_of"):
        _CFG.extra["as_of"] = body["as_of"]


def _classify(resp, ep: str) -> tuple[bool, str | None]:
    """(ok, err_class). Validate cả status LẪN cấu trúc response — 2xx sai schema KHÔNG phải success."""
    status = getattr(resp, "status_code", 0) or 0
    if status == 0:
        return False, "conn_fail"
    if status in (401, 403):
        return False, "auth_denied"      # → INVALID_TEST
    if status == 400:
        return False, "bad_request"      # → INVALID_TEST (fingerprint/payload)
    if status == 429:
        return False, "rate_limited"
    if status == 504:
        return False, "server_504"
    if 500 <= status < 600:
        return False, "server_5xx"
    if status != 200:
        return False, "client_4xx"
    # 200 → kiểm cấu trúc
    try:
        body = resp.json()
    except Exception:
        return False, "schema_invalid"
    if not valid_success_body(ep, body):
        return False, "schema_invalid"
    return True, None


class ApiUser(HttpUser):
    wait_time = constant_pacing(1)  # 1 iteration/giây/VU (closed-model)

    def on_start(self):
        global _user_counter
        if not self.host:
            self.host = _CFG.base_url
        _user_counter += 1
        self._corpus = UserCorpus(_CFG.load_seed, _user_counter)
        self._headers = {}
        if _CFG.api_key:
            self._headers["X-API-Key"] = _CFG.api_key
        if _CFG.auth_bearer:
            self._headers["Authorization"] = f"Bearer {_CFG.auth_bearer}"
        # pagination state
        self._page_token: str | None = None
        self._page_filters: dict | None = None
        self._page_sort: str | None = None
        self._ops_toggle = 0

    # ── một iteration = một request ───────────────────────────────────────────
    @task
    def one_request(self):
        scn = _CFG.scenario_def
        if scn.ops_only:
            self._do_ops()
        else:
            r = self._corpus.rand()
            if r < scn.weight_search:
                self._do_search()
            else:
                self._do_market()

    # ── /jobs/search với pagination 80/20 ─────────────────────────────────────
    def _do_search(self):
        want_next = (self._page_token is not None
                     and self._corpus.rand() < C.NEXT_PAGE_PROB)
        if want_next:
            # next-page dùng ĐÚNG filter/sort đã gắn với token (state nhất quán).
            body = {"filters": self._page_filters, "sort": self._page_sort,
                    "limit": C.SEARCH_LIMIT, "page_token": self._page_token}
            page_kind = "next"
            new_filters = self._page_filters
            new_sort = self._page_sort
        else:
            payload = self._corpus.search_payload(C.SEARCH_LIMIT)
            body = payload
            new_filters, new_sort = payload["filters"], payload["sort"]  # CHƯA commit vào state
            page_kind = "first"
        resp, rt, ok, err, phase = self._fire("POST", "/v1/jobs/search", C.EP_SEARCH, json_body=body)
        if ok:
            # Commit ATOMIC: token luôn khớp filter/sort đã gửi (tránh ghép token cũ với filter mới).
            token = (resp.json() or {}).get("next_page_token")
            self._page_filters, self._page_sort, self._page_token = new_filters, new_sort, token
        elif base_error_class(err) == "bad_request":
            # 400 = lỗi payload/fingerprint (corpus sai hoặc token lệch) → workload/script không hợp lệ,
            # KHÔNG phải lỗi hiệu năng — cả first-page LẪN next-page.
            _COLLECTOR.trigger_invalid(
                f"search 400 ({'next-page fingerprint' if page_kind == 'next' else 'first-page payload'})")
        else:
            # first-page lỗi khác: KHÔNG đụng state cũ (chuỗi next-page đang có vẫn nhất quán).
            # next-page lỗi khác: hỏng chuỗi → xoá token, lần sau bắt đầu search mới.
            if page_kind == "next":
                self._page_token = None
        self._record(C.EP_SEARCH, phase, rt, ok, err, page_kind)

    def _do_market(self):
        _resp, rt, ok, err, phase = self._fire("GET", "/v1/market/metrics", C.EP_MARKET,
                                               params=self._corpus.market_params())
        self._record(C.EP_MARKET, phase, rt, ok, err, None)

    def _do_ops(self):
        self._ops_toggle ^= 1
        if self._ops_toggle:
            _resp, rt, ok, err, phase = self._fire("GET", "/health", C.EP_HEALTH)
            self._record(C.EP_HEALTH, phase, rt, ok, err, None)
        else:
            _resp, rt, ok, err, phase = self._fire("GET", "/v1/metadata", C.EP_METADATA)
            self._record(C.EP_METADATA, phase, rt, ok, err, None)

    # ── I/O + phân loại ───────────────────────────────────────────────────────
    def _fire(self, method: str, path: str, ep: str, *, json_body=None, params=None):
        # Chốt phase Ở THỜI ĐIỂM BẮT ĐẦU request (không đọc lại lúc kết thúc) → request chậm đi qua
        # ranh giới pha vẫn được quy về đúng pha nó bắt đầu, tránh làm bẩn reference/recovery.
        phase = PHASE.get() or "unknown"
        name = f"{phase}:{ep}"
        run_id = f"lt_{_CFG.run_id}:{phase}:{uuid.uuid4().hex[:16]}"
        headers = {**self._headers, "X-Request-ID": run_id}
        start = time.perf_counter()
        try:
            with self.client.request(method, path, name=name, headers=headers,
                                     json=json_body, params=params,
                                     timeout=C.HTTP_TIMEOUT, catch_response=True) as resp:
                rt = (time.perf_counter() - start) * 1000
                ok, err = _classify(resp, ep)
                ok, err = enforce_duration_limit(ok, err, rt)
                if ok:
                    resp.success()
                    _capture_as_of(resp, ep)
                else:
                    resp.failure(err or "error")
                return resp, rt, ok, err, phase
        except rex.ReadTimeout:
            return None, (time.perf_counter() - start) * 1000, False, "read_timeout", phase
        except rex.ConnectTimeout:
            return None, (time.perf_counter() - start) * 1000, False, "connect_timeout", phase
        except rex.ConnectionError:
            return None, (time.perf_counter() - start) * 1000, False, "conn_fail", phase
        except rex.RequestException:
            return None, (time.perf_counter() - start) * 1000, False, "conn_fail", phase

    def _record(self, ep, phase, rt, ok, err, page_kind):
        _COLLECTOR.record(ep, phase, rt, ok, err, page_kind)
        base_err = base_error_class(err)
        # INVALID_TEST: auth/400 → dừng ngay
        if base_err == "auth_denied":
            _COLLECTOR.trigger_invalid(f"{ep}: {err} (kiểm API key/bearer)")
        if base_err == "bad_request" and ep != C.EP_SEARCH:
            _COLLECTOR.trigger_invalid(f"{ep}: 400 bad_request")
        if _COLLECTOR.invalid or _COLLECTOR.aborted:
            self.environment.runner.quit()
            return
        # 429 trong pha năng lực → abort ngay
        if base_err == "rate_limited" and phase in C.CAPACITY_PHASES:
            _COLLECTOR.trigger_abort(f"429 trong pha năng lực '{phase}' (rate-limit cản tải)")
            self.environment.runner.quit()
            return
        reason = _COLLECTOR.check_abort(phase)
        if reason:
            self.environment.runner.quit()
