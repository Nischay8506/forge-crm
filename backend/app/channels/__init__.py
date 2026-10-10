"""Channel fabric (Step 6, F12.5): every message to or from a customer or a member
of staff goes through here.

    send.queue()      the only way to ask for a message to go out: runs the checks
                      (checks.py) and leaves it queued, held, waiting for a language
                      review or blocked
    send.deliver_due  the worker's job (every 10 seconds): re-checks each due message
                      and hands it to the channel's adapter, at most once
    send.receive()    an incoming message: matched to a contact only on one exact
                      address, its language recognised, STOP handled
    consent.py        consent per contact and channel; STOP and the unsubscribe link
    rules.py          each company's limits, quiet hours and test mode (L8, L11)
    adapters/         the providers: the simulator (test inbox) and SMTP (L2 A).
                      Nothing outside this package may import them or set a message's
                      status other than "draft" (tests/test_channels.py checks this).

Email only for now (L1); WhatsApp and SMS are on the later list (docs/HANDOVER.md
section 7) and plug into the same path.
"""
