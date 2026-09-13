# AiMenu terminal bridge / ტერმინალის ხიდი

Runs on the till PC next to the bank's card terminal. The POS starts a card
payment, the bridge drives the terminal, the result lands in the ledger.

```
pip install -r requirements.txt
cp bridge.example.toml bridge.toml   # paste the key from Dashboard → Card terminals
python aimenu_terminal_bridge.py
python aimenu_terminal_bridge.py --simulate   # training mode, approves everything
```

`protocol = "bog"` / `"tbc"` need the bank's ECR kit — the adapters in
`adapters/` are placeholders until then and refuse every job with a clear
error. `protocol = "sim"` approves everything after two seconds.

---

გაუშვით სალაროს კომპიუტერზე, ბანკის ტერმინალის გვერდით. POS იწყებს ბარათით
გადახდას, ხიდი ტერმინალს მართავს, შედეგი ავტომატურად აღირიცხება.
