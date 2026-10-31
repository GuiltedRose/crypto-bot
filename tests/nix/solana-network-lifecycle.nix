# UNEXECUTED isolated NixOS VM test. No internet/node/bootstrap traffic.
{ pkgs ? import <nixpkgs> {} }:
pkgs.testers.runNixOSTest {
  name = "solana-network-lifecycle";
  nodes.machine = { pkgs, ... }: {
    imports = [ ../../nix/solana-network-isolation.nix ];
    services.solana-budget-isolation = {
      enable = true;
      services = [ "solana-fixture" ];
      uplinkInterface = "eth1";
    };
    environment.systemPackages = [ pkgs.sqlite pkgs.iproute2 pkgs.iputils ];
    users.users.solana-fixture = { isSystemUser = true; group = "solana-fixture"; };
    users.groups.solana-fixture = {};
    systemd.services.solana-fixture.serviceConfig = {
      User = "solana-fixture";
      ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
    };
  };
  testScript = ''
    machine.start()
    machine.wait_for_unit("multi-user.target")
    # Enabling module must not start network or workload.
    machine.fail("systemctl is-active solana-budget-network.service")
    machine.fail("systemctl is-active solana-fixture.service")
    # Missing ledger fails closed; workload cannot start or get a namespace.
    machine.fail("systemctl start solana-fixture.service")
    machine.fail("ip link show solana-host")
    machine.succeed("systemctl show solana-fixture -p NetworkNamespacePath | grep /run/netns/solana-budget")
    machine.succeed("systemctl show solana-fixture -p NoNewPrivileges | grep yes")
    # Create a synthetic current-cycle ledger only inside the isolated VM.
    machine.succeed("date -s '2026-10-15 12:00:00 UTC'")
    machine.succeed("${pkgs.python3}/bin/python ${../../solana_network_budget.py} --ledger /var/lib/solana-network/budget.sqlite initialize --start 2026-10-01T00:00:00Z --end 2026-11-01T00:00:00Z")
    machine.succeed("systemctl reset-failed solana-budget-network solana-fixture")
    machine.succeed("systemctl start solana-fixture")
    machine.wait_for_unit("solana-fixture")
    # Compare actual process net namespaces, not just declared unit properties.
    machine.succeed("pid=$(systemctl show solana-fixture -p MainPID --value); test $(readlink /proc/$pid/ns/net) = $(ip netns exec solana-budget readlink /proc/self/ns/net)")
    machine.fail("ip netns exec solana-budget ping -c 1 -W 1 169.254.203.1")
    machine.succeed("systemctl stop solana-budget-network")
    machine.fail("systemctl is-active solana-fixture")
    machine.fail("ip link show solana-host")
    first = int(machine.succeed("sqlite3 /var/lib/solana-network/budget.sqlite 'select sum(bytes) from grants'"))
    # A restarted controller/workload must charge a new grant, never restore old quota.
    machine.succeed("systemctl start solana-fixture")
    machine.succeed("systemctl stop solana-budget-network")
    second = int(machine.succeed("sqlite3 /var/lib/solana-network/budget.sqlite 'select sum(bytes) from grants'"))
    assert second > first > 0

  '';
}
