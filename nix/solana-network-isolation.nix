# UNTESTED opt-in isolation only. Never starts a node or provisions a WAN route.
{ config, lib, ... }:
let cfg = config.services.solana-budget-isolation;
in {
  options.services.solana-budget-isolation = {
    enable = lib.mkEnableOption "fail-closed Solana network isolation";
    services = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [];
      description = "Every native node, bootstrap, repair, RPC and observer systemd service; no omissions.";
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [{ assertion = cfg.services != []; message = "Explicit complete Solana service inventory required."; }];
    systemd.services = lib.genAttrs cfg.services (_: {
      serviceConfig = {
        NetworkNamespacePath = "/run/netns/solana-budget";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        RestrictNamespaces = true;
        # Intentionally blocked pending operator validation of kernel policy.
        # Operator must replace this only after quota, shaping and expiry tests.
        IPAddressDeny = "any";
      };
    });
  };
}
