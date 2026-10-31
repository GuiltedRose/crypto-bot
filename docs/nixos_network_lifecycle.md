# NixOS Solana network lifecycle — UNTESTED

No build, tests, deployment, privileged network commands or services were run.
The existing application flake is retained; no host flake is created. Unit-test
success from previous revisions does not validate this new kernel/service wiring.
Independent mainnet feasibility remains unproven under the hardware/transfer limits.

## Declarative installation

Import the existing default module or standalone isolation module, update the
host's pinned input, and configure your complete existing workload inventory:

```nix
services.solana-budget-isolation = {
  enable = true;
  services = [ "crypto-bot-indexer" ]; # add EVERY node/bootstrap/observer/helper
  uplinkInterface = "enp3s0";          # your actual WAN-facing host interface
  ledgerFile = "/var/lib/solana-network/budget.sqlite";
  sessionBytes = 1000000;
  uploadKbytes = 16;
  downloadKbytes = 16;
  reserveSession = false;
};
```

Each listed workload must already have an explicit non-root systemd `User` and
its own application/state configuration. The module creates no validator, observer
or trading workload. It removes automatic wantedBy startup and restart loops for
listed units. Do not combine this module with timers, sockets, path activators,
manual processes or other units that start those workloads automatically. Existing
services must be stopped deliberately before the first activation; review the NixOS
dry-activation output before switch. Rollback does not restore accounting or grants.

Only controller code and nonsensitive configuration paths enter the Nix store;
no repository tree, secrets, wallet state or databases are copied there. The
ledger must be root-owned, owner-only and persistent. Initialize billing dates
explicitly using `crypto-bot-network-budget --ledger PATH initialize ...` as in
[network budget](solana_network_budget.md). A missing, corrupted, expired or
insecure ledger prevents setup. Nix activation never initializes or resets it.
Custom ledger parent directories need explicit root-owned creation by the operator.

## Lifecycle

`solana-budget-network.service` is not wanted by any target. A manual workload
start requires the controller; READY is sent only after namespace, quota, host
boundary, NAT, shaping, routes and initial accounting validation succeed. The
host-facing veth stays down until enforcement is installed. The quota is one
shared ingress/egress allowance, with a kernel expiry of at most 60 seconds.
The controller ends the session by 55 seconds (setup time included), on quota
exhaustion, accounting failure or SIGTERM. No automatic renewal or catch-up occurs.
A new manual start charges a new grant; unused/crashed grants are never refunded.

All listed workloads share `/run/netns/solana-budget`. No capabilities, namespace
creation or Unix/packet sockets are allowed; host-facing proxy paths and IPv6 are
blocked. The boundary uses the reserved 169.254.203.0/30 subnet, `solana-host` veth,
namespace `solana-budget`, `solana-peer` during setup and dedicated nft tables
`solana_boundary`, `solana_nat`, `solana_budget`. Reserve these names exclusively.
Namespace-existing conflicts fail startup. Owned stale state is cleaned on stop.
No whole-host firewall flush is used. NixOS forwarding is enabled only with this
option; its forward firewall allows the dedicated source/uplink pair and established
return flow. Existing independent firewall rules may still block connectivity;
never remove household protections to make a test pass.

Listed units BindsTo the controller and stop when it terminates. ExecStopPost
lowers/removes the veth and owned namespace on failures, including killed
controllers. Kernel expiry is the fallback if systemd/Python cleanup is delayed.
The ledger and root-owned `network-events.jsonl` persist grants and explicit gap
markers. Collector checkpoints stay in their existing application state; no
network cutoff fabricates coverage or advances them.

The 120GB normal cap, explicitly authorized reserve and 145GB operational cutoff
(150GB minus 5GB safety margin) are unchanged. `reserveSession=true` designates an
explicit essential recovery session; the ledger must also have reserve authorization.
Optional observer checks still suspend at 120GB. Rates are configurable, and tc
shaping plus DSCP CS1 reduces burst/priority impact. Router SQM/QoS remains necessary
for household-wide priority and ISP accounting reconciliation.

## Server operator verification — not run

1. Evaluate/build the host configuration and review dry activation. Verify there
   are no new workload auto-start dependencies. Back up the budget ledger.
2. Run ordinary unit tests for `test_solana_network_budget.py` and
   `test_solana_network_lifecycle.py` without privileged networking.
3. Run the isolated kernel quota/expiry tests from the network-budget guide on
   the monolith only. The additional NixOS VM test is
   `tests/nix/solana-network-lifecycle.nix`; build it explicitly using your pinned
   nixpkgs. It tests opt-in startup, containment properties and missing-ledger
   failure without any internet/default route or node.
4. After activation, `systemctl is-active solana-budget-network` should be inactive;
   application units should remain inactive. Inspect `systemctl cat UNIT` and
   `systemctl show UNIT -p NetworkNamespacePath -p User -p CapabilityBoundingSet`.
5. Only when ready, start a bounded session manually:
   `sudo systemctl start solana-budget-network.service`.
   This creates connectivity infrastructure but starts no application itself.
   Inspect `sudo ip netns exec solana-budget nft list table inet solana_budget`,
   `sudo nft list table inet solana_boundary` and `sudo tc -s qdisc show dev solana-host`.
   It expires automatically. A manually started workload can also require a fresh
   session; this is authorization for that configured grant, not unlimited traffic.
6. Inspect `journalctl -u solana-budget-network`, persistent network-events and
   `crypto-bot-network-budget --ledger PATH report`. Confirm consumption increases
   on fresh starts and never decreases on failed starts/rebuilds/rollback.
7. Disable safely: stop all listed workloads, stop the controller, verify veth is
   absent and applications inactive, then set `enable=false` and rebuild. Keep
   ledger/history. Do not delete accounting to repair startup failures. Re-enabling
   requires manual starts and the same billing ledger.

## Explicit limitations / acceptance still required

- These components have not been executed. Kernel rules, Nix options and systemd
  ordering need validation on the monolith; this is not a protection guarantee.
- Inventory containment covers only listed services and their children. Root,
  privileged admins, omitted processes, inherited network FDs, socket-activated
  workloads, tunnels and externally started helpers cannot be guaranteed contained.
  Use dedicated users and prohibit external activators/proxies; audit the host.
- The host uses IPv4 NAT. DNS must be configured through external resolver IPs
  reachable within quota; a host 127.0.0.53 resolver is not usable inside this
  namespace. No host resolver proxy or unsolicited inbound P2P port forwarding is
  supplied. Solana mainnet reachability/sync is therefore not demonstrated.
- The controller needs root to manage networking. Do not assign privileges to
  workloads. An administrator can bypass/delete rules or roll back accounting
  backups; Nix rollback alone cannot do that, and this module never restores a DB.
- Kernel-policy reload/removal by other firewall managers, clock jumps, NIC
  offloads and ISP framing require server/router testing. Keep nftables managed
  by NixOS; do not concurrently flush tables. Native kernel counters are sampled
  each second; crashes can lose recent measured counters, but the entire issued
  grant remains charged. Excess unsolicited inbound packets remain an ISP risk.
- Short sessions intentionally do not provide a sustained node or automatic
  reconnection loop. They allow bounded verification before any larger operating
  decision. Full-node operation remains NO-GO without demonstrated resource fit.
