{
  description = "Read-only Solana indexer and isolated paper trading";
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/6f06d204d8f2d8761d9da78778745974a3216381";
  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
      environments = eachSystem (system:
        let
          pkgs = import nixpkgs { inherit system; };
          wheels = builtins.fromJSON (builtins.readFile ./nix/solders-wheels.json);
          python = pkgs.python313.override {
            packageOverrides = final: prev: {
              jsonalias = final.buildPythonPackage {
                pname = "jsonalias";
                version = "0.1.1";
                format = "wheel";
                src = pkgs.fetchurl {
                  url = "https://files.pythonhosted.org/packages/41/ed/05aebce69f78c104feff2ffcdd5a6f9d668a208aba3a8bf56e3750809fd8/jsonalias-0.1.1-py3-none-any.whl";
                  hash = "sha256-pW0oiOY5eBLGBhVlBOhh6OwA4YgAWvFJ8APHh9s9Pxg=";
                };
                pythonImportsCheck = [ "jsonalias" ];
              };
              solders = final.buildPythonPackage {
                pname = "solders";
                version = "0.29.0";
                format = "wheel";
                src = pkgs.fetchurl wheels.${system};
                nativeBuildInputs = pkgs.lib.optionals pkgs.stdenv.isLinux [ pkgs.autoPatchelfHook ];
                buildInputs = pkgs.lib.optionals pkgs.stdenv.isLinux [ pkgs.stdenv.cc.cc.lib ];
                dependencies = [ final.jsonalias final.typing-extensions ];
                pythonImportsCheck = [ "solders.pubkey" "solders.transaction" ];
              };
              websockets = final.buildPythonPackage {
                pname = "websockets";
                version = "15.0.1";
                pyproject = true;
                src = pkgs.fetchurl {
                  url = "https://files.pythonhosted.org/packages/21/e6/26d09fab466b7ca9c7737474c52be4f76a40301b08362eb2dbc19dcc16c1/websockets-15.0.1.tar.gz";
                  hash = "sha256-glRN4CB2uvugOM4FXuZBLWjaE6tH8MYMq4JzRt6Cje4=";
                };
                build-system = [ final.setuptools ];
                pythonImportsCheck = [ "websockets" ];
                doCheck = false;
              };
            };
          };
        in { inherit pkgs; python = python.withPackages (p: [ p.solders p.websockets ]); });
      # Explicit source filter: no state, .env, private config, wallet files or venv.
      source = nixpkgs.lib.cleanSourceWith {
        src = ./.;
        filter = path: type:
          let rel = nixpkgs.lib.removePrefix (toString ./. + "/") (toString path);
          in if type == "directory" then builtins.elem rel [ "vendor" "tests" "tests/fixtures" "tests/fixtures/onchain" ]
          else nixpkgs.lib.hasSuffix ".py" rel
            || (nixpkgs.lib.hasPrefix "vendor/" rel && nixpkgs.lib.hasSuffix ".json" rel)
            || (nixpkgs.lib.hasPrefix "tests/fixtures/onchain/" rel && nixpkgs.lib.hasSuffix ".json" rel)
            || builtins.elem rel [ "meme_live_rules.json" "pump_watch_specs.json" "assets.json" ];
      };
    in {
      packages = eachSystem (system:
        let inherit (environments.${system}) pkgs python;
        in {
          tmux = pkgs.tmux;
          default = pkgs.stdenvNoCC.mkDerivation {
            pname = "crypto-bot-paper";
            version = "0.1.0";
            src = source;
            nativeBuildInputs = [ pkgs.makeWrapper ];
            dontBuild = true;
            installPhase = ''
              mkdir -p $out/share/crypto-bot $out/bin
              cp *.py meme_live_rules.json pump_watch_specs.json assets.json $out/share/crypto-bot/
              cp -r vendor $out/share/crypto-bot/
              makeWrapper ${python}/bin/python $out/bin/crypto-bot-indexer \
                --add-flags "$out/share/crypto-bot/run_onchain_indexer.py"
              makeWrapper ${python}/bin/python $out/bin/crypto-bot-paper \
                --add-flags "$out/share/crypto-bot/run_paper.py"
            '';
          };
        });
      apps = eachSystem (system: {
        default = self.apps.${system}.indexer;
        indexer = { type = "app"; program = "${self.packages.${system}.default}/bin/crypto-bot-indexer"; };
        tmux = { type = "app"; program = "${environments.${system}.pkgs.tmux}/bin/tmux"; };
        paper = { type = "app"; program = "${self.packages.${system}.default}/bin/crypto-bot-paper"; };
      });
      devShells = eachSystem (system:
        let inherit (environments.${system}) pkgs python;
        in { default = pkgs.mkShell { packages = [ python pkgs.sqlite pkgs.tmux ]; }; });
      checks = eachSystem (system:
        let inherit (environments.${system}) pkgs python;
        in { indexer = pkgs.runCommand "crypto-bot-indexer-tests" { nativeBuildInputs = [ python ]; } ''
          cp -r ${source} source
          chmod -R u+w source
          cd source
          python -m unittest discover -s tests -p test_onchain_indexer.py
          touch $out
        ''; });
      nixosModules.default = { config, lib, pkgs, ... }:
        import ./nix/module.nix {
          inherit config lib pkgs;
          package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        };
    };
}
