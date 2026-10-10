"""The simulator (decision L11 A): nothing leaves Forge. Every message lands in the
Test inbox, where a person can reply as the customer, bounce it or press its
unsubscribe link. Three fixed addresses act out what can go wrong:

    bounce@simulator.test   the mail server refuses the address (bounced)
    outage@simulator.test   the mail server cannot be reached (tried again later)
    lost@simulator.test     the connection drops mid-send (failed, check before resending)
"""

from app.channels.adapters.base import (
    BOUNCED,
    RETRY,
    SENT,
    UNKNOWN,
    OutgoingEmail,
    SendResult,
)

NAME = "simulator"
FROM_ADDRESS = "forge@simulator.test"
BOUNCE, OUTAGE, LOST = "bounce@simulator.test", "outage@simulator.test", "lost@simulator.test"


class SimulatorAdapter:
    name = NAME

    async def send(self, mail: OutgoingEmail) -> SendResult:
        to = mail.to.strip().lower()
        if to == BOUNCE:
            return SendResult(BOUNCED, reason="The mail server said this address does not "
                                              "exist (simulated)")
        if to == OUTAGE:
            return SendResult(RETRY, reason="The mail server could not be reached "
                                            "(simulated)")
        if to == LOST:
            return SendResult(UNKNOWN, reason="The connection dropped while sending "
                                              "(simulated)")
        return SendResult(SENT, provider_ref=f"<{mail.message_id}@simulator.test>")

    async def check(self) -> tuple[bool, str]:
        return True, "The simulator is always ready; nothing leaves Forge"
