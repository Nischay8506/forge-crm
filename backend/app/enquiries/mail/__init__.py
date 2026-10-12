"""Reading the company's sales mailbox (decision N3 A): Gmail through the Gmail API,
Microsoft 365 through Microsoft Graph (both signed in with OAuth, Forge's own app
registrations, N4 A), and any other provider over IMAP. Every source checks every
minute; Gmail and Microsoft can also tell Forge at once when mail arrives (push),
which only makes Forge check sooner: nothing in a push is trusted.

A mailbox is read from the moment it is connected; older mail is left alone.
"""
