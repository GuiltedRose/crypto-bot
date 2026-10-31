# UNEXECUTED isolated VM test, pinned pkgs supplied by the application flake.
{ pkgs }:
pkgs.testers.runNixOSTest {
  name = "solana-network-lifecycle";
  defaults = { ... }: {
    networking.useDHCP = false;
    networking.defaultGateway = null;
    networking.defaultGateway6 = null;
    services.timesyncd.enable = false;
    networking.nameservers = [];
    virtualisation.vlans = [ 1 ];
    environment.systemPackages = [ pkgs.sqlite pkgs.iproute2 pkgs.iputils pkgs.nftables pkgs.procps ];
  };
  nodes.peer = {
    networking.interfaces.eth1.ipv4.addresses = [{ address = "10.81.0.2"; prefixLength = 24; }];
    networking.firewall.enable = false; # fixture peer only, no uplink/default route
  };
  nodes.machine = { lib, ... }: {
    imports = [ ../../nix/solana-network-isolation.nix ];
    networking.interfaces.eth1.ipv4.addresses = [{ address = "10.81.0.1"; prefixLength = 24; }];
    services.solana-budget-isolation = {
      enable = true;
      services = [ "solana-fixture" ];
      uplinkInterface = "eth1";
      sessionBytes = 4096;
    };
    specialisation.reconfigured.configuration.services.solana-budget-isolation.uploadKbytes = lib.mkForce 8;
    users.users.solana-fixture = { isSystemUser = true; group = "solana-fixture"; };
    users.groups.solana-fixture = {};
    systemd.services.solana-fixture.serviceConfig = {
      User = "solana-fixture";
      ExecStart = "${pkgs.coreutils}/bin/sleep infinity";
    };
  };
  testScript = ''
    import json
    start_all()
    for host in (machine, peer):
        host.wait_for_unit("multi-user.target")
        assert not host.succeed("ip -4 route show default").strip()
        assert not host.succeed("ip -6 route show default").strip()
    machine.fail("systemctl is-active solana-budget-network.service")
    machine.fail("systemctl is-active solana-fixture.service")

    def usage():
        return int(machine.succeed("sqlite3 /var/lib/solana-network/budget.sqlite 'select coalesce(sum(bytes),0) from grants'"))

    def begin():
        machine.succeed("systemctl reset-failed solana-budget-network solana-fixture")
        machine.succeed("systemctl start solana-fixture")
        machine.wait_for_unit("solana-fixture")

    def ping(count=1):
        return machine.execute(f"ip netns exec solana-budget ping -n -c {count} -i 0.1 -W 1 10.81.0.2")

    # Missing/invalid accounting cannot create connectivity.
    machine.fail("systemctl start solana-fixture")
    machine.fail("ip link show solana-host")
    machine.succeed("printf broken > /var/lib/solana-network/budget.sqlite; chmod 600 /var/lib/solana-network/budget.sqlite")
    machine.succeed("systemctl reset-failed solana-budget-network solana-fixture")
    machine.fail("systemctl start solana-fixture")
    machine.fail("ip link show solana-host")
    machine.succeed("rm /var/lib/solana-network/budget.sqlite") # corrupt fixture only
    machine.succeed("date -s '2026-10-15 12:00:00 UTC'")
    machine.succeed("${pkgs.python3}/bin/python ${../../solana_network_budget.py} --ledger /var/lib/solana-network/budget.sqlite initialize --start 2026-10-01T00:00:00Z --end 2026-11-01T00:00:00Z")
    begin()
    # READY means quota, routes and both shapers are already present.
    machine.succeed("ip netns exec solana-budget nft list quota inet solana_budget shared")
    machine.succeed("tc qdisc show dev solana-host | grep tbf")
    machine.succeed("ip netns exec solana-budget tc qdisc show dev eth0 | grep tbf")
    machine.succeed("pid=$(systemctl show solana-fixture -p MainPID --value); test $(readlink /proc/$pid/ns/net) = $(ip netns exec solana-budget readlink /proc/self/ns/net)")
    machine.succeed("systemctl show solana-fixture -p NoNewPrivileges | grep yes")
    machine.fail("ip netns exec solana-budget ping -c 1 -W 1 169.254.203.1")
    assert ping()[0] == 0
    state = json.loads(machine.succeed("ip netns exec solana-budget nft -j list table inet solana_budget"))
    counters = {x["counter"]["name"]: x["counter"]["bytes"] for x in state["nftables"] if "counter" in x}
    assert counters["download"] > 0 and counters["upload"] > 0
    quotas = [x["quota"] for x in state["nftables"] if "quota" in x]
    assert len(quotas) == 1 and quotas[0]["used"] >= counters["download"] + counters["upload"]
    ping(100) # exceeds the same 4096-byte bidirectional allowance
    machine.wait_until_fails("systemctl is-active solana-fixture", timeout=20)
    machine.wait_until_fails("ip link show solana-host", timeout=20)
    first = usage()
    begin()
    assert usage() > first
    # Freeze userspace accounting: the kernel lease must still expire.
    machine.succeed("kill -STOP $(systemctl show solana-budget-network -p MainPID --value)")
    machine.sleep(61)
    assert ping()[0] != 0
    machine.succeed("kill -CONT $(systemctl show solana-budget-network -p MainPID --value)")
    machine.wait_until_fails("systemctl is-active solana-fixture", timeout=20)
    machine.wait_until_fails("ip link show solana-host", timeout=20)
    before = usage()
    # Switch configuration and back: ledger must not be reconstructed/reset.
    base = machine.succeed("readlink -f /run/current-system").strip()
    machine.succeed(f"{base}/specialisation/reconfigured/bin/switch-to-configuration test")
    assert usage() == before
    machine.succeed(f"{base}/bin/switch-to-configuration test")
    assert usage() == before
    machine.fail("systemctl is-active solana-budget-network")
    begin()
    assert usage() > before
    machine.succeed("systemctl stop solana-budget-network")
    machine.fail("systemctl is-active solana-fixture")
    assert not machine.succeed("ip -4 route show default").strip()
  '';
}
