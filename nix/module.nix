{ config, lib, pkgs, package }:
let
  cfg = config.services.crypto-bot-paper;
  inherit (lib) mkOption mkEnableOption types mkIf escapeShellArg;
  state = cfg.stateDirectory;
  index = "${state}/index/index.sqlite";
  common = {
    wantedBy = [ "multi-user.target" ];
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    environment = {
      PYTHONUNBUFFERED = "1";
      PYTHONDONTWRITEBYTECODE = "1";
      CRYPTO_BOT_PROVIDER_LIMITS = "${state}/provider_limits.sqlite";
    };
    serviceConfig = {
      User = "crypto-bot-paper";
      Group = "crypto-bot-paper";
      WorkingDirectory = state;
      Restart = "on-failure";
      RestartSec = 30;
      UMask = "0077";
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      RestrictSUIDSGID = true;
      CapabilityBoundingSet = "";
      ReadWritePaths = [ state ];
      TimeoutStopSec = 45;
    };
    # Bound systemd restart loops as well as the application's reconnect attempts.
    startLimitIntervalSec = 600;
    startLimitBurst = 5;
  };
in {
  imports = [ ./solana-network-isolation.nix ];
  options.services.crypto-bot-paper = {
    enable = mkEnableOption "local read-only indexer deployment (no live service)";
    package = mkOption { type = types.package; default = package; };
    stateDirectory = mkOption {
      type = types.str; default = "/var/lib/crypto-bot-paper";
      description = "External persistent state; never put this under /nix/store.";
    };
    indexerConfigFile = mkOption {
      type = types.str;
      description = "Runtime-only absolute config path; loaded as a systemd credential. Never use a Nix path literal or builtins.readFile for secrets.";
    };
    startSlot = mkOption { type = types.nullOr types.ints.unsigned; default = null; };
    batchSlots = mkOption { type = types.ints.between 1 64; default = 8; };
    maxCatchupSlots = mkOption { type = types.ints.between 1 64; default = 64; };
    paper = {
      enable = mkEnableOption "separate isolated paper service";
      rulesFile = mkOption { type = types.str; default = "${cfg.package}/share/crypto-bot/meme_live_rules.json"; };
      watchlistFile = mkOption { type = types.nullOr types.str; default = null; };
      # Initial capital is required explicitly, and is ignored on normal resumes.
      initialBalanceUsd = mkOption { type = types.number; default = 6; };
      mints = mkOption { type = types.listOf types.str; default = []; };
    };
  };
  config = mkIf cfg.enable {
    assertions = [
      { assertion = lib.hasPrefix "/" state && !(lib.hasPrefix "/nix/store/" state);
        message = "crypto-bot stateDirectory must be an external absolute path."; }
      { assertion = lib.hasPrefix "/" cfg.indexerConfigFile && !(lib.hasPrefix "/nix/store/" cfg.indexerConfigFile);
        message = "Indexer credentials/config must be a runtime path outside the Nix store."; }
      { assertion = cfg.maxCatchupSlots >= cfg.batchSlots;
        message = "maxCatchupSlots must be at least batchSlots."; }
    ];
    users.groups.crypto-bot-paper = {};
    users.users.crypto-bot-paper = {
      isSystemUser = true;
      group = "crypto-bot-paper";
    };
    systemd.tmpfiles.rules = [
      "d ${state} 0700 crypto-bot-paper crypto-bot-paper -"
      "d ${state}/index 0700 crypto-bot-paper crypto-bot-paper -"
      "d ${state}/paper 0700 crypto-bot-paper crypto-bot-paper -"
    ];
    systemd.services.crypto-bot-indexer = lib.recursiveUpdate common {
      description = "Finalized Solana Pump indexer (read-only RPC)";
      serviceConfig.LoadCredential = "indexer.json:${cfg.indexerConfigFile}";
      script = ''
        exec ${cfg.package}/bin/crypto-bot-indexer \
          --config "$CREDENTIALS_DIRECTORY/indexer.json" \
          --db ${escapeShellArg index} --loop \
          --batch-slots ${toString cfg.batchSlots} --max-catchup-slots ${toString cfg.maxCatchupSlots} \
          ${lib.optionalString (cfg.startSlot != null) "--start-slot ${toString cfg.startSlot}"}
      '';
    };
    systemd.services.crypto-bot-paper = mkIf cfg.paper.enable (lib.recursiveUpdate common {
      description = "Isolated local-index paper trader (no signing)";
      after = common.after ++ [ "crypto-bot-indexer.service" ];
      wants = common.wants ++ [ "crypto-bot-indexer.service" ];
      script = ''
        exec ${cfg.package}/bin/crypto-bot-paper \
          --state-dir ${escapeShellArg "${state}/paper"} --onchain-db ${escapeShellArg index} \
          --rules ${escapeShellArg cfg.paper.rulesFile} --exploratory \
          --balance ${escapeShellArg (toString cfg.paper.initialBalanceUsd)} --assume-usd-costs \
          --discover --loop --interval 30 \
          ${lib.optionalString (cfg.paper.watchlistFile != null) "--watchlist ${escapeShellArg cfg.paper.watchlistFile}"} \
          ${lib.concatMapStringsSep " " (mint: "--mint ${escapeShellArg mint}") cfg.paper.mints}
      '';
    });
  };
}
