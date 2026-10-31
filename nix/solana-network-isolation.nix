# UNTESTED, opt-in server lifecycle. No wantedBy: workloads require manual start.
{ config, lib, pkgs, ... }:
let
  cfg = config.services.solana-budget-isolation;
  inherit (lib) mkOption types;
  runtime = pkgs.writeText "solana-network-policy.json" (builtins.toJSON {
    ledger = cfg.ledgerFile;
    uplink = cfg.uplinkInterface;
    inherit (cfg) sessionBytes uploadKbytes downloadKbytes reserveSession;
  });
  support = pkgs.runCommand "solana-network-controller" {} ''
    mkdir -p $out
    cp ${../solana_network_lifecycle.py} $out/solana_network_lifecycle.py
    cp ${../solana_network_budget.py} $out/solana_network_budget.py
  '';
  controller = "${pkgs.python3}/bin/python ${support}/solana_network_lifecycle.py";
  path = [ pkgs.iproute2 pkgs.nftables pkgs.procps pkgs.coreutils ];
in {
  options.services.solana-budget-isolation = {
    enable = lib.mkEnableOption "bounded, isolated Solana network sessions";
    services = mkOption { type = types.listOf types.str; default = []; description = "Complete inventory of unprivileged Solana service names, without .service."; };
    uplinkInterface = mkOption { type = types.str; default = ""; };
    ledgerFile = mkOption { type = types.str; default = "/var/lib/solana-network/budget.sqlite"; };
    sessionBytes = mkOption { type = types.ints.between 1 10000000; default = 1000000; };
    uploadKbytes = mkOption { type = types.ints.between 1 1024; default = 16; };
    downloadKbytes = mkOption { type = types.ints.between 1 1024; default = 16; };
    reserveSession = mkOption { type = types.bool; default = false; description = "Explicit essential recovery session; ledger reserve authorization still required above 120GB."; };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = cfg.services != [] && !(builtins.elem "solana-budget-network" cfg.services); message = "Explicit complete Solana service inventory required."; }
      { assertion = builtins.match "[a-zA-Z0-9_.-]+" cfg.uplinkInterface != null; message = "Explicit uplink interface required."; }
      { assertion = lib.hasPrefix "/" cfg.ledgerFile && !(lib.hasPrefix "/nix/store/" cfg.ledgerFile); message = "Budget ledger must be an external persistent absolute path."; }
    ] ++ map (name: {
      assertion = (config.systemd.services.${name}.serviceConfig.User or "root") != "root"
        && (config.systemd.services.${name}.serviceConfig.User or "") != "";
      message = "Solana workload ${name} requires an explicit non-root User.";
    }) cfg.services;
    boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
    networking.nftables.enable = true;
    networking.firewall.filterForward = true;
    networking.firewall.extraForwardRules = ''
      iifname "solana-host" oifname "${cfg.uplinkInterface}" ip saddr 169.254.203.2 accept
      iifname "${cfg.uplinkInterface}" oifname "solana-host" ip daddr 169.254.203.2 ct state established,related accept
    '';
    systemd.tmpfiles.rules = [ "d /var/lib/solana-network 0700 root root -" ];
    systemd.services = {
      solana-budget-network = {
        description = "Single bounded Solana namespace session (no automatic renewal)";
        inherit path;
        restartIfChanged = false;
        stopIfChanged = true;
        after = [ "network-online.target" "nftables.service" "firewall.service" ];
        wants = [ "network-online.target" ];
        serviceConfig = {
          Type = "notify";
          NotifyAccess = "main";
          ExecStart = "${controller} --config ${runtime}";
          ExecStopPost = "${controller} --cleanup";
          Restart = "no";
          TimeoutStartSec = 45;
          TimeoutStopSec = 30;
          UMask = "0077";
          Environment = "PYTHONPATH=${support}";
        };
      };
    } // lib.genAttrs cfg.services (_: {
      wantedBy = lib.mkForce [];
      restartIfChanged = false;
      stopIfChanged = true;
      requires = [ "solana-budget-network.service" ];
      bindsTo = [ "solana-budget-network.service" ];
      after = [ "solana-budget-network.service" ];
      serviceConfig = {
        NetworkNamespacePath = "/run/netns/solana-budget";
        NoNewPrivileges = true;
        CapabilityBoundingSet = lib.mkForce "";
        AmbientCapabilities = lib.mkForce "";
        RestrictNamespaces = true;
        RestrictAddressFamilies = [ "AF_INET" "AF_NETLINK" ];
        ProtectSystem = "strict";
        ProtectHome = true;
        InaccessiblePaths = [ "-/run/netns" "-/var/lib/solana-network" ];
        Restart = lib.mkForce "no";
      };
    });
  };
}
