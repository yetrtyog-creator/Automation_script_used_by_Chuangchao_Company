from typing import Any, Dict, List, Optional

from config_loader import SearchConfig, ResultDisplayConfig
from vast_api_client import VastAPIClient
from logging_utils import setup_logger


class OfferSearcher:
    def __init__(
        self,
        client: VastAPIClient,
        search_config: SearchConfig,
        results_config: ResultDisplayConfig,
        logger=None,
    ):
        self.client = client
        self.search_config = search_config
        self.results_config = results_config
        self.logger = logger or setup_logger(__name__)

    def search_with_fallback(self) -> List[Dict[str, Any]]:
        offers = self._try_full()
        if offers:
            return offers

        if self.search_config.relax.enable_geo_relax:
            offers = self._try_relax_geo()
            if offers:
                return offers

        offers = self._try_relax_price()
        if offers:
            return offers

        offers = self._try_minimal()
        if offers:
            return offers

        self.logger.error("No offers found after all strategies.")
        return []

    def display_offers(self, offers: List[Dict[str, Any]]) -> None:
        limit = self.results_config.limit
        self.logger.info("Found %d offers, showing top %d", len(offers), limit)

        header = (
            f"{'ID':>8} {'GPU':>14} {'vRAM':>6} {'$/h':>10} "
            f"{'$/h_total':>10} {'inet_down':>10} {'inet_up':>8} {'country':>10}"
        )
        print(header)
        print("-" * len(header))

        for o in offers[:limit]:
            oid = o.get("id", "")
            gpu = o.get("gpu_name", "")
            vram = o.get("gpu_ram", "")
            dph = o.get("dph", "")
            dph_total = o.get("dph_total", "")
            inet_down = o.get("inet_down", "")
            inet_up = o.get("inet_up", "")
            country = o.get("country", "") or o.get("geolocation", "")
            print(
                f"{str(oid):>8} {str(gpu):>14} {str(vram):>6} {str(dph):>10} "
                f"{str(dph_total):>10} {str(inet_down):>10} {str(inet_up):>8} {str(country):>10}"
            )

    def select_best_offer(self, offers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not offers:
            return None

        key = "dph_total" if self.search_config.use_dph_total else "dph"

        def price(o: Dict[str, Any]) -> float:
            v = o.get(key)
            try:
                return float(v)
            except (TypeError, ValueError):
                return float("inf")

        best = min(offers, key=price)
        self.logger.info(
            "Selected offer %s with %s=%s",
            best.get("id"),
            key,
            best.get(key),
        )
        return best

    # ---------- strategies ----------

    def _try_full(self) -> List[Dict[str, Any]]:
        self.logger.info("Searching with full criteria...")
        return self.client.search_offers()

    def _try_relax_geo(self) -> List[Dict[str, Any]]:
        self.logger.info("Relaxing geolocation constraints...")
        original = list(self.search_config.geolocations)
        try:
            self.search_config.geolocations = []
            return self.client.search_offers()
        finally:
            self.search_config.geolocations = original

    def _try_relax_price(self) -> List[Dict[str, Any]]:
        self.logger.info("Relaxing price constraints per config...")
        r = self.search_config.relax
        orig_min = self.search_config.min_dph
        orig_max = self.search_config.max_dph
        orig_geo = list(self.search_config.geolocations)
        try:
            self.search_config.min_dph = orig_min * r.price_min_factor
            self.search_config.max_dph = orig_max * r.price_max_factor
            self.search_config.geolocations = []
            return self.client.search_offers()
        finally:
            self.search_config.min_dph = orig_min
            self.search_config.max_dph = orig_max
            self.search_config.geolocations = orig_geo

    def _try_minimal(self) -> List[Dict[str, Any]]:
        self.logger.info("Trying minimal constraints search...")
        original = {
            "geolocations": list(self.search_config.geolocations),
            "min_dph": self.search_config.min_dph,
            "max_dph": self.search_config.max_dph,
            "bandwidth_min_down": self.search_config.bandwidth_min_down,
            "bandwidth_min_up": self.search_config.bandwidth_min_up,
        }
        try:
            self.search_config.geolocations = []
            self.search_config.min_dph = None
            self.search_config.max_dph = None
            self.search_config.bandwidth_min_down = None
            self.search_config.bandwidth_min_up = None
            return self.client.search_offers()
        finally:
            self.search_config.geolocations = original["geolocations"]
            self.search_config.min_dph = original["min_dph"]
            self.search_config.max_dph = original["max_dph"]
            self.search_config.bandwidth_min_down = original["bandwidth_min_down"]
            self.search_config.bandwidth_min_up = original["bandwidth_min_up"]
