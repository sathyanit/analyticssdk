"""Optional synthetic fixture. Run only against a disposable demo database."""
import argparse
import random
from datetime import datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from analytics_app.models import Event
from analytics_app.storage import Store, utcnow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='analytics.db')
    args = parser.parse_args()
    store = Store(args.database)
    rng = random.Random(42)
    accepted = 0
    today = utcnow().date()
    with store.connect() as db:
        for offset in range(7):
            day = today - timedelta(days=6-offset)
            for visit in range(rng.randint(24, 75)):
                moment = datetime.combine(day, time.min, timezone.utc) + timedelta(seconds=rng.randrange(86400))
                if moment > utcnow():
                    continue
                visitor = f'fixture-visitor-{rng.randrange(90)}'
                for step, name in enumerate(['page_view', 'menu_viewed'] + (['cart_added', 'checkout_started', 'order_created'] if visit % 5 == 0 else [])):
                    event = Event(event_id=uuid5(NAMESPACE_URL, f'fixture:{day}:{visit}:{step}'), name=name,
                                  anonymous_id=visitor, session_id=f'fixture-session-{day}-{visit}',
                                  occurred_at=moment, path='/demo' if name != 'order_created' else None,
                                  properties={'synthetic': True})
                    accepted += store.insert(db, event, 'server' if name == 'order_created' else 'browser')
    print(f'Added {accepted} explicitly synthetic events to {args.database}. No real orders created.')


if __name__ == '__main__':
    main()
