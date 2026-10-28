"""Read-only reconciliation of the one-time inventory transaction. Never retries."""
import json
from pathlib import Path
from config import LiveConfig
from live import Rpc,LiveOrders


def main():
    if not Path('state/setup.sqlite').exists():
        raise ValueError('No setup journal found')
    config=LiveConfig.from_env()
    orders=LiveOrders(Rpc(config.rpc_url),'state/setup.sqlite')
    rows=orders.db.execute('SELECT order_id,signature FROM orders').fetchall()
    for order_id,signature in rows:
        print(json.dumps({'order_id':order_id,'signature':signature,'status':orders.reconcile(order_id)}))


if __name__=='__main__':
    try:
        main()
    except Exception as error:
        print('Setup reconciliation failed ('+type(error).__name__+'); no transaction submitted.')
        raise SystemExit(1)
