import os
import shutil
import subprocess
import json
from typing import Any, Dict, List, Optional

from config_loader import SearchConfig, TemplateConfig, AuthConfig, CliConfig
from logging_utils import setup_logger


class VastAPIClient:
    def __init__(
        self,
        search_config: SearchConfig,
        template_config: TemplateConfig,
        auth_config: AuthConfig,
        cli_config: CliConfig,
        logger=None,
    ):
        self.search_config = search_config
        self.template_config = template_config
        self.auth_config = auth_config
        self.cli_config = cli_config
        self.logger = logger or setup_logger(__name__)
        self.cli_path = self._find_cli()

    # ---------- CLI ----------

    def _find_cli(self) -> str:
        for name in self.cli_config.executable_candidates:
            path = shutil.which(name)
            if path:
                self.logger.debug(f"Using vast CLI: {path}")
                return path
        raise RuntimeError(
            f"None of CLI executables found: {', '.join(self.cli_config.executable_candidates)}"
        )

    # ---------- Query Build ----------

    def build_query(self) -> List[str]:
        sc = self.search_config
        q: List[str] = []

        # 地區
        if sc.geolocations:
            geo_expr = ",".join(sc.geolocations)
            q.append(f"geolocation in [{geo_expr}]")

        # 硬碟
        if sc.disk_space_gb_min is not None:
            q.append(f"disk_space>={sc.disk_space_gb_min}")

        # 價格 (dph / dph_total)
        if sc.use_dph_total:
            if sc.min_dph is not None:
                q.append(f"dph_total>={sc.min_dph}")
            if sc.max_dph is not None:
                q.append(f"dph_total<={sc.max_dph}")
        else:
            if sc.min_dph is not None:
                q.append(f"dph>={sc.min_dph}")
            if sc.max_dph is not None:
                q.append(f"dph<={sc.max_dph}")

        # 頻寬
        if sc.bandwidth_min_down is not None:
            q.append(f"inet_down>={sc.bandwidth_min_down}")
        if sc.bandwidth_min_up is not None:
            q.append(f"inet_up>={sc.bandwidth_min_up}")

        # ---- 布林欄位：改成 True/False / 支援 None = 不加條件 ----
        def bool_lit(v: Optional[bool]) -> Optional[str]:
            if v is True:
                return "True"
            if v is False:
                return "False"
            return None  # None 代表呼叫端想「不要限制」

        v = bool_lit(sc.external)
        if v is not None:
            q.append(f"external={v}")

        v = bool_lit(sc.rentable)
        if v is not None:
            q.append(f"rentable={v}")

        v = bool_lit(sc.verified)
        if v is not None:
            q.append(f"verified={v}")

        v = bool_lit(sc.rented)
        if v is not None:
            q.append(f"rented={v}")

        return q


    # ---------- GPU Filter ----------

    def _filter_by_gpu(self, offers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        targets = self.search_config.gpu_names
        if not targets:
            return offers

        norm_targets = [
            t.replace(" ", "").replace("_", "").upper() for t in targets
        ]

        filtered: List[Dict[str, Any]] = []
        for o in offers:
            gpu_name = str(o.get("gpu_name", ""))
            norm_gpu = gpu_name.replace(" ", "").replace("_", "").upper()
            if any(t in norm_gpu or norm_gpu in t for t in norm_targets):
                filtered.append(o)

        self.logger.info("GPU filter: %d → %d offers", len(offers), len(filtered))
        if filtered:
            sample = {str(o.get("gpu_name", "")) for o in filtered[:5]}
            self.logger.debug("Matched GPUs: %s", ", ".join(sample))
        else:
            self.logger.debug(
                "No offers matched gpu_names=%s",
                ", ".join(self.search_config.gpu_names),
            )
        return filtered

    # ---------- Search Offers ----------

    def search_offers(self) -> List[Dict[str, Any]]:
        sc = self.search_config

        cmd: List[str] = [self.cli_path, "search", "offers", "--raw"]

        if sc.rental_type:
            cmd += ["--type", sc.rental_type]

        if sc.order_by:
            cmd += ["-o", sc.order_by]

        query_args = self.build_query()
        if query_args:
            self.logger.debug("Built query: %s", " ".join(query_args))
            cmd += query_args

        if self.auth_config.method == "arg":
            cmd += ["--api-key", self.auth_config.api_key]
        elif self.auth_config.method == "env":
            if not os.getenv("VAST_API_KEY"):
                raise RuntimeError("auth.method=env but VAST_API_KEY not set")

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = self.cli_config.pythonioencoding

        self.logger.debug("Running: %s", " ".join(cmd))

        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=sc.timeout_seconds,
            env=env,
        )

        if res.returncode != 0:
            self.logger.error("vast search offers failed: %s", res.stderr.strip())
            raise RuntimeError("vast search offers failed")

        stdout = res.stdout.strip()
        if not stdout:
            self.logger.error("vast search offers returned empty output")
            return []

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError as e:
            self.logger.error(
                "Invalid JSON from vast search offers: %s\nSTDOUT:\n%s\nSTDERR:\n%s",
                e,
                stdout,
                res.stderr.strip(),
            )
            raise

        if isinstance(data, dict) and "offers" in data:
            offers = data.get("offers") or []
        elif isinstance(data, list):
            offers = data
        else:
            self.logger.error("Unexpected response format from vast search offers")
            return []

        if not isinstance(offers, list):
            self.logger.error("Unexpected offers format (not a list)")
            return []

        return self._filter_by_gpu(offers)

    # ---------- Create Instance ----------

    def create_instance(self, offer_id: Optional[int]) -> int:
        tpl = self.template_config

        if tpl.mode == "template_only":
            if not tpl.template_hash:
                raise RuntimeError("template_hash required for template_only mode")
            cmd = [
                self.cli_path,
                "create",
                "instance",
                "--template_hash",
                tpl.template_hash,
            ]
        else:
            if offer_id is None:
                raise ValueError("offer_id is required when mode is not template_only")
            cmd = [
                self.cli_path,
                "create",
                "instance",
                str(offer_id),
            ]
            if tpl.mode == "docker_image" and tpl.docker_image:
                cmd += ["--image", tpl.docker_image]
            elif tpl.mode == "template_merge":
                if not tpl.template_hash:
                    raise RuntimeError("template_hash required for template_merge mode")
                cmd += ["--template_hash", tpl.template_hash]

        cmd += ["--disk", str(tpl.disk_gb)]
        if tpl.ssh:
            cmd.append("--ssh")
        if tpl.direct:
            cmd.append("--direct")

        if self.auth_config.method == "arg":
            cmd += ["--api-key", self.auth_config.api_key]

        env = os.environ.copy()
        env["PYTHONIOENCODING"] = self.cli_config.pythonioencoding

        self.logger.info("Creating instance with command: %s", " ".join(cmd))
        res = subprocess.run(cmd, env=env)
        return res.returncode
