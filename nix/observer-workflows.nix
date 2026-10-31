# Operator-enabled real observer/ML workflows. No automatic trading service.
{ config, lib, pkgs, package }:
let
  cfg=config.services.crypto-bot-observer;
  state=cfg.stateDirectory;
in {
  options.services.crypto-bot-observer = {
    enable=lib.mkEnableOption "controlled production observer workload";
    package=lib.mkOption { type=lib.types.package; default=package; };
    configFile=lib.mkOption { type=lib.types.str; description="Reviewed runtime JSON outside the Nix store; credentials never read during evaluation."; };
    stateDirectory=lib.mkOption { type=lib.types.str; default="/var/lib/crypto-bot-observer"; };
    maxCycles=lib.mkOption { type=lib.types.ints.between 1 10000; default=1; };
    learning = {
      enable=lib.mkEnableOption "offline bounded SVM/LSTM/RAG worker";
      inputFile=lib.mkOption { type=lib.types.str; default=""; description="Reviewed normalized JSONL; no automatic source rights grant."; };
    };
  };
  config=lib.mkIf cfg.enable {
    assertions=[
      { assertion=lib.hasPrefix "/" state && !(lib.hasPrefix "/nix/store" state); message="Observer state must be an external persistent directory."; }
      { assertion=lib.hasPrefix "/" cfg.configFile && !(lib.hasPrefix "/nix/store" cfg.configFile); message="Observer config must be an external runtime path."; }
      { assertion=(!cfg.learning.enable) || (lib.hasPrefix "/" cfg.learning.inputFile && !(lib.hasPrefix "/nix/store" cfg.learning.inputFile)); message="ML needs a reviewed external input path."; }
    ];
    services.solana-budget-isolation.services=[ "crypto-bot-observer" ];
    services.solana-budget-isolation.enable=true;
    users.groups.crypto-bot-observer={};
    users.users.crypto-bot-observer={ isSystemUser=true; group="crypto-bot-observer"; };
    systemd.tmpfiles.rules=["d ${state} 0700 crypto-bot-observer crypto-bot-observer -" "d ${state}/ml 0700 crypto-bot-observer crypto-bot-observer -"];
    systemd.services.crypto-bot-observer={
      description="Bounded Solana observer; manual start, no trading";
      serviceConfig={
        User="crypto-bot-observer";
        LoadCredential="observer.json:${cfg.configFile}";
        ReadWritePaths=[state];
        UMask="0077";
        MemoryAccounting=true;
      };
      script=''
        exec ${cfg.package}/bin/crypto-bot-observer --config "$CREDENTIALS_DIRECTORY/observer.json" \
          --db ${lib.escapeShellArg "${state}/observer.sqlite"} --batch-slots 1 --interval 5 --max-cycles ${toString cfg.maxCycles}
      '';
    };
    systemd.services.crypto-bot-unified-learning=lib.mkIf cfg.learning.enable {
      description="Offline bounded unified ML; forecasts never authorize orders";
      wantedBy=[];
      restartIfChanged=false;
      serviceConfig={
        User="crypto-bot-observer";
        Type="oneshot";
        PrivateNetwork=true;
        NoNewPrivileges=true;
        CapabilityBoundingSet="";
        ProtectSystem="strict";
        ProtectHome=true;
        ReadWritePaths=["${state}/ml"];
        LoadCredential="observations.jsonl:${cfg.learning.inputFile}";
        UMask="0077";
        CPUQuota="25%";
        Nice=10;
        MemoryHigh="512M";
        MemoryMax="1G";
        MemorySwapMax=0;
        RuntimeMaxSec=300;
        TimeoutStartSec=300;
        Restart="no";
      };
      script=''
        initialization=""
        if [ ! -e ${lib.escapeShellArg "${state}/ml/unified.sqlite"} ]; then initialization="--initialize"; fi
        exec ${cfg.package}/bin/crypto-bot-unified-learning \
          --db ${lib.escapeShellArg "${state}/ml/unified.sqlite"} $initialization \
          --input "$CREDENTIALS_DIRECTORY/observations.jsonl" --train all
      '';
    };
  };
}
