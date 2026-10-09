# A local ERPNext for checking Forge's ERP connection

These files start a throwaway ERPNext on your own computer with Docker Desktop, so you
can connect Forge to it the way a customer would (decision E1). They are for testing
only: every password in them is `admin`.

| File | ERPNext | Opens on |
| --- | --- | --- |
| `compose.yml` | 16.50.0 | http://localhost:8085 |
| `compose-v15.yml` | 15.122.0 | http://localhost:8086 |

You need Docker Desktop running and about 4 GB of free memory. Run the commands from the
`forge-crm` folder.

## 1. Start ERPNext (first time: 5 to 15 minutes)

```
docker compose -f infra/erpnext/compose.yml up -d
docker compose -f infra/erpnext/compose.yml logs -f create-site
```

Wait until the log says the site was created and the `create-site` container exits, then
press Ctrl+C. Restart the ERPNext servers once, because API keys made on a brand-new site
can fail with "Failed to decrypt key" until they restart:

```
docker compose -f infra/erpnext/compose.yml restart backend frontend
```

## 2. Finish ERPNext's own setup

1. Open http://localhost:8085 and log in as `Administrator`, password `admin`.
2. Complete the setup wizard: country India, currency INR, time zone Asia/Kolkata, any
   company name. Tick "Generate demo data" if you want sample customers, orders and
   invoices to look at.

## 3. Make a user for Forge and its API key

1. In ERPNext open **User › Add User**, for example `forge.sync@yourcompany.com`.
2. Give it the roles **Sales User**, **Accounts User**, **Stock User** and
   **Sales Master Manager**. Forge only needs to read, plus create customers when asked.
3. Save, then open the user's **Settings › API Access** and click **Generate Keys**.
   Copy the API key and the API secret (the secret is shown only once).

## 4. Connect Forge (the Done-When check: under one day, no developer)

Start Forge as `docs/HANDOVER.md` describes (backend, worker and frontend), with
`CREDENTIAL_KEYS` set in `backend/.env`. Then, in Forge:

1. **Integrations › Connect an ERP**.
2. Name it, enter `http://localhost:8085`, the API key and the secret, and click
   **Test connection**. If something is wrong, Forge says what to fix.
3. Leave every record type ticked and click **Save connection**. The connection page
   shows each record type being read. In our check on ERPNext 16 the first full read
   finished 51 seconds after saving.
4. Open **Customers** to match the ERP customers Forge was not sure about, and
   **Code lists** to confirm the suggested unit, group and tax codes.

## 5. Check that a broken login is caught (Done-When: within 5 minutes)

1. In ERPNext open the Forge user again and click **Generate Keys**. The old secret stops
   working.
2. Within about a minute Forge shows a red banner on Integrations, a red dot on the menu
   and an alert in **Decisions › Waiting for you** that says ERPNext rejected the key and
   what to do. (Our check: 36 to 41 seconds.)
3. Enter the new key and secret on the connection's **Settings and login** tab. The alert
   closes on its own.

## Stopping and starting again

```
docker compose -f infra/erpnext/compose.yml stop     # keeps the data
docker compose -f infra/erpnext/compose.yml start
docker compose -f infra/erpnext/compose.yml down -v  # deletes the site and its data
```

If the page shows "502 Bad Gateway" after a restart, restart `frontend` as well.
