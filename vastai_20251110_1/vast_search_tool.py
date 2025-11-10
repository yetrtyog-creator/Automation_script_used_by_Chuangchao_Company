import sys
import logging
from pathlib import Path
from argparse import ArgumentParser

from logging_utils import setup_logger
from config_loader import ConfigManager, ConfigError
from vast_api_client import VastAPIClient
from offer_searcher import OfferSearcher


class VastSearchTool:
    def __init__(self, config_path: Path, debug: bool = False):
        level = logging.DEBUG if debug else logging.INFO
        self.logger = setup_logger("vast_search", level=level)

        self.logger.debug("Loading config from %s", config_path)
        self.config_manager = ConfigManager(config_path, self.logger)
        app_cfg = self.config_manager.load()

        self.client = VastAPIClient(
            app_cfg.search,
            app_cfg.template,
            app_cfg.auth,
            app_cfg.cli,
            logger=self.logger,
        )
        self.searcher = OfferSearcher(
            self.client,
            app_cfg.search,
            app_cfg.results,
            logger=self.logger,
        )
        self.template_cfg = app_cfg.template

    def run(self) -> int:
        offers = self.searcher.search_with_fallback()
        if not offers:
            return 1

        self.searcher.display_offers(offers)
        best = self.searcher.select_best_offer(offers)
        if not best:
            self.logger.error("No suitable offer to create instance from.")
            return 1

        offer_id = best.get("id")
        if offer_id is None:
            self.logger.error("Best offer has no 'id' field.")
            return 1

        try:
            choice = input(f"Create instance from best offer ID {offer_id}? [y/N]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 130

        if choice != "y":
            self.logger.info("Skipped instance creation.")
            return 0

        rc = self.client.create_instance(int(offer_id))
        if rc == 0:
            self.logger.info("Instance created successfully.")
        else:
            self.logger.error("Instance creation failed with code %s", rc)
        return rc


def parse_args(argv: list):
    parser = ArgumentParser(description="Vast.ai GPU search helper")
    parser.add_argument(
        "-c",
        "--config",
        required=True,
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args(argv)


def main(argv: list = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)

    cfg_path = Path(args.config)
    try:
        tool = VastSearchTool(cfg_path, debug=args.debug)
        return tool.run()
    except ConfigError as e:
        print(f"❌ Config error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
