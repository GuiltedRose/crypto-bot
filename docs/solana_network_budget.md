# Solana network ceiling — UNTESTED, deployment disabled

No node, tests, benchmarks, firewall, namespace, shaper or collector was executed.

## Decision

Independent mainnet replay remains **NO-GO on the current resources and transfer
budget**. No complete sync or sustainable replay within 150GB/month has been
shown. The official [Anza requirements](https://docs.anza.xyz/operations/requirements)
recommend symmetric 1Gbps connectivity for an unstaked node. That is a connection
recommendation, not a measured monthly transfer figure. Our 120GB/30-day allowance
averages only about 0.37Mbps combined. Bootstrap alone needs separate measurement
and budget; no unlimited initial synchronization is authorized.

Current implementation supplies conservative accounting/policy components, not
an enabled independent node or a validated automatic network deployment. No
external RPC replacement is substituted.

## Limits

Decimal billing units: 120GB normal; 30GB reserve needs the explicit
`authorize-reserve --confirm-reserve` command for the active billing cycle.
A 5GB safety margin stops new authorization at 145GB even with reserve enabled.
Optional observer collection stays suspended at 120GB; reserve does not silently
resume it. All bootstrap, synchronization, repairs, recovery, RPC, DNS, peer and
native process traffic must share the same namespace and quota.

`solana_network_budget.py` durably debits each small grant before rendering a
firewall. Uninstalled or partially unused grants are never refunded. Reboots,
crashes and reconnects require a NEW grant; never reinstall an old grant/ruleset.
Missing/corrupt/expired ledgers fail closed. Billing boundaries require explicit
ISO timestamps with timezone; there is no automatic reset. Preserve the previous
ledger before manually provisioning a new billing cycle at the real ISP boundary.
Do not create parallel ledgers for one household cycle.

Kernel counters track namespace ingress and egress before policing/quota drops,
including protocol traffic and retransmissions. Counters are sampled by
`sample-os`; decreases or mismatched grant IDs are errors, not free capacity.
The charged budget is the greater of reserved or observed bytes for each grant.
Reports show both directions, remaining allowances and a projection from elapsed
cycle usage. Missing samples make usage incomplete; grants still remain charged.
The projection is not a forecast of future traffic or household consumption.

## Isolation and enforcement contract

Every Solana process must run unprivileged in `/run/netns/solana-budget`, without
NET_ADMIN/SYS_ADMIN or ability to move namespaces. This includes node children,
bootstrap/download utilities, observer, indexing and any network-enabled helpers.
Local loopback requests are excluded from WAN accounting; their external peer
traffic is included. Do not attach another NIC, tunnel, host socket proxy or route
that bypasses the namespace. A namespace is not proof of independent execution.

`nix/solana-network-isolation.nix` supplies opt-in systemd restrictions for an
explicit complete service inventory. It does not start services. By default it
also denies all IP access. Import it separately into the server configuration;
it is not silently enabled by the existing flake/module. No namespace, NAT or
WAN route is automatically provisioned. An absent namespace must prevent startup.

The nft renderer provides default-drop input/output/forward chains and one shared
bidirectional quota. Each grant also expires after at most 60 seconds (or sooner
at the billing boundary). Never reset/reuse a quota. Missing policy means no
routing is permitted; keep the host-facing veth down until policy installation
and validation. Lease exhaustion stops IP acceptance even if Python dies. Kernel
counters, including denied inbound traffic, must be persisted before replacing a
table; on failure keep connectivity off. There is no automatic grant renewal.

Bandwidth defaults are 16 decimal kbytes/s in each direction (nft units),
with explicit configurable rates and bounded burst behavior. `render-shaping`
prints tc TBF commands for server-side upload and root-veth download shaping.
It assumes namespace interface `eth0` and root veth `solana-host`; adapt deliberately.
These commands are NOT executed by Python. nft rate limits are policing, not
smooth shaping. Output marks DSCP CS1 for low-priority treatment. Actual household
priority needs router SQM/QoS that recognizes the dedicated Solana host/subnet and
puts work traffic ahead of it. Host download shaping cannot recover WAN capacity
already consumed. Do not overwrite a shared household qdisc without review.

IP-byte counters do not capture Ethernet/ISP framing, and unsolicited ingress can
arrive after cutoff. The 5GB margin is a planning cushion, not a mathematically
hard ISP guarantee. Router/WAN monitoring and a dedicated router kill switch are
needed to reconcile ISP totals and stop persistent incoming flows. Hardware
NIC/offload behavior, nft syntax/kernel support, lease expiry, IPv4/IPv6, reboot,
process containment and prioritization remain unverified on the server.

## Operator-only commands (not executed)

Use an external, root-owned persistent ledger directory; never Nix-store or wallet
state. Substitute your actual ISP billing dates, not calendar-month assumptions.

```bash
python solana_network_budget.py --ledger /var/lib/solana-network/budget.sqlite initialize \
  --start '<CYCLE_START_ISO_WITH_OFFSET>' --end '<CYCLE_END_ISO_WITH_OFFSET>'
python solana_network_budget.py --ledger /var/lib/solana-network/budget.sqlite report
python solana_network_budget.py --ledger /var/lib/solana-network/budget.sqlite render-shaping
python -m unittest discover -s tests -p 'test_solana_network_budget.py'
python -m unittest discover -s tests -p 'test_onchain_stream_candles.py'
```

`grant --bytes 1000000` debits a grant and prints nft configuration only; it does
not install policy or start a process. `sample-os --grant-id ID` reads OS counters
using `ip netns exec ... nft -j ...`; do not invoke it on the laptop. The raw
`sample` command supports offline fixtures, not trusted production measurement.
Before any server collection, the operator must validate isolated rules with
synthetic traffic, denied paths, IPv6, kernel accounting and expiration. Tests
mock privileged commands and do not validate kernel enforcement themselves.

The observer examples include a budget-ledger path. Missing/expired accounting or
normal-cap suspension records an explicit stream gap and preserves finalized
checkpoints. Coverage remains incomplete until explicitly budgeted gap recovery;
no zeros or automatic jump-to-head are inserted. Other runners/native nodes need
the OS isolation regardless of Python hooks. No trade, NN or wallet state changed.

An additional **server-only** kernel smoke-test program is supplied, not run:
`SOLANA_SERVER_KERNEL_TESTS=I_AM_ON_THE_MONOLITH python -m unittest discover -s tests -p 'test_solana_network_kernel.py'`.
It requires root and iproute2/nftables/ping, creates two isolated local namespaces
with no internet/default route, then tests shared quota and monitor-independent
lease expiry. Normal test discovery skips it. It does not validate router QoS,
ISP counters, offloads or whole-service containment.
