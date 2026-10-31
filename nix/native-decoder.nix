# Evaluated only after the operator has generated/reviewed Cargo.lock.
{ pkgs, python }:
python.pkgs.buildPythonPackage {
  pname = "crypto-bot-solana-binary";
  version = "0.1.0";
  pyproject = true;
  src = pkgs.lib.cleanSourceWith {
    src = ../native/solana_binary;
    filter = path: type:
      if type == "directory" then builtins.baseNameOf path != "target"
      else builtins.elem (builtins.baseNameOf path) [ "Cargo.toml" "Cargo.lock" "pyproject.toml" "lib.rs" ];
  };
  cargoDeps = pkgs.rustPlatform.importCargoLock { lockFile = ../native/solana_binary/Cargo.lock; };
  nativeBuildInputs = [ pkgs.rustPlatform.cargoSetupHook ];
  build-system = [ pkgs.rustPlatform.maturinBuildHook ];
  pythonImportsCheck = [ "_solana_binary" ];
  doCheck = false; # Operator runs explicit offline differential tests.
}
