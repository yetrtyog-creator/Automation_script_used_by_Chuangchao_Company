import sys
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


class ConfigError(Exception):
    pass


@dataclass
class SearchRelaxConfig:
    enable_geo_relax: bool
    price_min_factor: float
    price_max_factor: float


@dataclass
class SearchConfig:
    gpu_names: List[str]
    geolocations: List[str]
    disk_space_gb_min: int
    min_dph: float
    max_dph: float
    use_dph_total: bool
    order_by: Optional[str]
    bandwidth_min_down: Optional[int]
    bandwidth_min_up: Optional[int]
    external: bool
    rentable: bool
    verified: bool
    rented: bool
    rental_type: Optional[str]
    timeout_seconds: int
    relax: SearchRelaxConfig


@dataclass
class TemplateConfig:
    mode: str
    docker_image: Optional[str]
    template_hash: Optional[str]
    disk_gb: int
    ssh: bool
    direct: bool


@dataclass
class AuthConfig:
    api_key: str
    persist: bool
    method: str  # 'arg' or 'env'


@dataclass
class ResultDisplayConfig:
    limit: int


@dataclass
class CliConfig:
    executable_candidates: List[str]
    pythonioencoding: str


@dataclass
class AppConfig:
    search: SearchConfig
    template: TemplateConfig
    auth: AuthConfig
    results: ResultDisplayConfig
    cli: CliConfig


class ConfigManager:
    def __init__(self, path: Path, logger: Optional[logging.Logger] = None):
        self.path = Path(path)
        self.logger = logger or logging.getLogger("vast_search")

    def load(self) -> AppConfig:
        if not self.path.exists():
            raise ConfigError(f"Config file not found: {self.path}")

        with self.path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        if not isinstance(raw, dict):
            raise ConfigError("Config root must be a mapping")

        search = self._parse_search(raw)
        template = self._parse_template(raw)
        auth = self._parse_auth(raw)
        results = self._parse_results(raw)
        cli = self._parse_cli(raw)

        return AppConfig(
            search=search,
            template=template,
            auth=auth,
            results=results,
            cli=cli,
        )

    # ----------------- helpers -----------------

    def _require(self, mapping: Dict[str, Any], key: str, desc: str) -> Any:
        if key not in mapping:
            raise ConfigError(f"Missing '{key}' in {desc}")
        return mapping[key]

    # ----------------- sections -----------------

    def _parse_search(self, raw: Dict[str, Any]) -> SearchConfig:
        gpu_names = self._require(raw, "gpu_names", "root.gpu_names")
        if not isinstance(gpu_names, list) or not gpu_names:
            raise ConfigError("gpu_names must be a non-empty list")

        geolocations = raw.get("geolocations") or []
        if not isinstance(geolocations, list):
            raise ConfigError("geolocations must be a list if present")

        disk_space_gb_min = self._require(raw, "disk_space_gb_min", "root.disk_space_gb_min")

        price = self._require(raw, "price", "root.price")
        min_dph = float(self._require(price, "min_dph", "price"))
        max_dph = float(self._require(price, "max_dph", "price"))
        use_dph_total = bool(price.get("use_dph_total", False))

        order_by_raw = str(raw.get("order_by") or "").strip()
        order_by = order_by_raw or None

        bandwidth = raw.get("bandwidth", {}) or {}
        bw_down = bandwidth.get("down_mbps")
        bw_up = bandwidth.get("up_mbps")
        bw_down_v = int(bw_down) if bw_down not in (None, 0, "0") else None
        bw_up_v = int(bw_up) if bw_up not in (None, 0, "0") else None

        inst = self._require(raw, "instance_filters", "root.instance_filters")
        external = bool(self._require(inst, "external", "instance_filters"))
        rentable = bool(self._require(inst, "rentable", "instance_filters"))
        verified = bool(self._require(inst, "verified", "instance_filters"))
        rented = bool(self._require(inst, "rented", "instance_filters"))

        rental_type_raw = raw.get("rental_type")
        rental_type: Optional[str] = None
        if rental_type_raw is not None:
            t = str(rental_type_raw).strip().lower()
            allowed = {"bid", "on-demand", "reserved"}
            if t not in allowed:
                raise ConfigError(
                    f"Invalid rental_type '{rental_type_raw}', must be one of {sorted(allowed)}"
                )
            rental_type = t

        search_sec = self._require(raw, "search", "root.search")
        timeout_seconds = int(self._require(search_sec, "timeout_seconds", "search"))

        relax = self._require(search_sec, "relax", "search.relax")
        enable_geo_relax = bool(self._require(relax, "enable_geo_relax", "search.relax"))
        price_min_factor = float(self._require(relax, "price_min_factor", "search.relax"))
        price_max_factor = float(self._require(relax, "price_max_factor", "search.relax"))

        # 驗證
        if min_dph > max_dph:
            raise ConfigError("price.min_dph cannot be greater than price.max_dph")
        if timeout_seconds <= 0:
            raise ConfigError("search.timeout_seconds must be positive")
        if not (0 < price_min_factor <= 1):
            raise ConfigError("search.relax.price_min_factor must be in (0, 1]")
        if price_max_factor < 1:
            raise ConfigError("search.relax.price_max_factor must be >= 1")

        return SearchConfig(
            gpu_names=[str(g).strip() for g in gpu_names],
            geolocations=[str(g).strip() for g in geolocations],
            disk_space_gb_min=int(disk_space_gb_min),
            min_dph=min_dph,
            max_dph=max_dph,
            use_dph_total=use_dph_total,
            order_by=order_by,
            bandwidth_min_down=bw_down_v,
            bandwidth_min_up=bw_up_v,
            external=external,
            rentable=rentable,
            verified=verified,
            rented=rented,
            rental_type=rental_type,
            timeout_seconds=timeout_seconds,
            relax=SearchRelaxConfig(
                enable_geo_relax=enable_geo_relax,
                price_min_factor=price_min_factor,
                price_max_factor=price_max_factor,
            ),
        )

    def _parse_template(self, raw: Dict[str, Any]) -> TemplateConfig:
        tpl = self._require(raw, "template", "root.template")
        mode = str(self._require(tpl, "mode", "template")).strip()
        allowed_modes = {"docker_image", "template_only", "template_merge"}
        if mode not in allowed_modes:
            raise ConfigError(f"template.mode must be one of {sorted(allowed_modes)}")

        docker_image = tpl.get("docker_image")
        template_hash = tpl.get("template_hash")
        disk_gb = int(self._require(tpl, "disk_gb", "template"))
        ssh = bool(tpl.get("ssh", False))
        direct = bool(tpl.get("direct", False))

        return TemplateConfig(
            mode=mode,
            docker_image=str(docker_image).strip() if docker_image else None,
            template_hash=str(template_hash).strip() if template_hash else None,
            disk_gb=disk_gb,
            ssh=ssh,
            direct=direct,
        )

    def _parse_auth(self, raw: Dict[str, Any]) -> AuthConfig:
        auth = self._require(raw, "auth", "root.auth")
        api_key = self._require(auth, "api_key", "auth.api_key")
        method = str(self._require(auth, "method", "auth.method")).strip()
        persist = bool(self._require(auth, "persist", "auth.persist"))

        if not isinstance(api_key, str) or not api_key.strip():
            raise ConfigError("auth.api_key is required and must be non-empty")
        if method not in ("arg", "env"):
            raise ConfigError("auth.method must be 'arg' or 'env'")

        return AuthConfig(
            api_key=api_key.strip(),
            persist=persist,
            method=method,
        )

    def _parse_results(self, raw: Dict[str, Any]) -> ResultDisplayConfig:
        results = raw.get("results", {})
        limit = results.get("display_limit")
        if limit is None:
            raise ConfigError("results.display_limit is required")
        if int(limit) <= 0:
            raise ConfigError("results.display_limit must be positive")
        return ResultDisplayConfig(limit=int(limit))

    def _parse_cli(self, raw: Dict[str, Any]) -> CliConfig:
        cli = raw.get("cli", {})
        candidates = cli.get("executable_candidates")
        if not candidates or not isinstance(candidates, list):
            raise ConfigError("cli.executable_candidates is required and must be non-empty list")

        pythonioencoding = cli.get("pythonioencoding")
        if not pythonioencoding:
            raise ConfigError("cli.pythonioencoding is required (e.g. 'utf-8')")

        return CliConfig(
            executable_candidates=[str(c) for c in candidates],
            pythonioencoding=str(pythonioencoding),
        )
