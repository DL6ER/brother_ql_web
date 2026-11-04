# Grocy

[Grocy](https://grocy.info) prints labels for products, stock entries, chores, recipes and batteries through a webhook. The label designer prints them with a saved label: Grocy sends its values as fields of their own, and each fills the blank of the same name.

## The label

Grocy sends every label to the same address, so one saved label serves products, stock entries, chores, recipes and batteries. Make it in the label designer and save it, e.g., as `Grocy`, or start from the demo label "Grocy" in the kitchen category: it prints the name Grocy sends, the Grocycode as a QR code and, for stock entries, the due date. These blanks take what Grocy sends:

| Blank | Content |
|---|---|
| `[product]` | The name of the product, for products and stock entries. Chores send `[chore]`, batteries `[battery]` and recipes `[recipe]` instead, so `[product][chore][battery][recipe]` in one line is the name of each. |
| `[grocycode]` | The Grocycode, e.g., `grcy:p:12` for a product or `grcy:p:12:6a0b1c2d3e4f5` for a stock entry, as the content of a QR code, which Grocy's barcode scanner reads. |
| `[due_date]` | The due date as Grocy writes it in its language, e.g., `DD: 2026-11-10`, in German `MHD: 2026-11-10`. Only for stock entries, with due dates turned on in Grocy. |
| `[stock_entry.best_before_date]` | The due date alone. Set the blank to Date in its settings, then it is printed in its format, e.g., `10.11.2026`. Products that never expire have `2999-12-31`. Only for stock entries: a date blank without a value prints today, so a label that also serves products takes `[due_date]`. |

A name with dots takes a value inside one of the objects Grocy sends, e.g., `[details.location.name]` for the location of the product. Values that are given in `blanks` go first, and the parameters of the request (`name`, `printer`, `model`, `print_count`, `cut_once`, `high_res`, `fast_printing`, `blanks`) are never taken for blanks.

## Grocy's settings

In Grocy's `config.php`, with the name the label is saved under (the label designer adds `.json`):

```php
Setting('FEATURE_FLAG_LABEL_PRINTER', true);
Setting('LABEL_PRINTER_WEBHOOK', 'http://<host>:8013/labeldesigner/api/repository/print');
Setting('LABEL_PRINTER_RUN_SERVER', true);
Setting('LABEL_PRINTER_PARAMS', ['name' => 'Grocy.json']);
Setting('LABEL_PRINTER_HOOK_JSON', true);
```

Or as environment variables, e.g., for Grocy in Docker. `LABEL_PRINTER_PARAMS` cannot be set there, so the name goes into the address:

```yaml
    environment:
      - GROCY_FEATURE_FLAG_LABEL_PRINTER=true
      - GROCY_LABEL_PRINTER_WEBHOOK=http://<host>:8013/labeldesigner/api/repository/print?name=Grocy.json
      - GROCY_LABEL_PRINTER_RUN_SERVER=true
      - GROCY_LABEL_PRINTER_HOOK_JSON=true
```

Further parameters, e.g., `printer` or `print_count`, go into `LABEL_PRINTER_PARAMS` or the address as well. Keep `LABEL_PRINTER_HOOK_JSON` on: without it Grocy sends a form, which has no `stock_entry` and of `details` only plain values such as `details[stock_amount]`, so `[details.location.name]` stays empty and `[stock_entry.best_before_date]` prints today. Keep `LABEL_PRINTER_RUN_SERVER` on as well: requests that Grocy's pages send from the browser are refused, because another website must not print through a visitor's browser.

## Trying it

Grocy prints a label with the "Print label" button of a product or a stock entry, or when stock is purchased with a label. Without Grocy, send what it sends:

```bash
curl -X POST 'http://localhost:8013/labeldesigner/api/repository/print?name=Grocy.json' \
  -H 'Content-Type: application/json' \
  -d '{"product": "Pesto", "grocycode": "grcy:p:12:6a0b1c2d3e4f5", "due_date": "DD: 2026-11-10", "stock_entry": {"best_before_date": "2026-11-10"}}'
```

`/labeldesigner/api/repository/preview` with the same fields returns the label as an image instead of printing it.
