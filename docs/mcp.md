# AI assistants (MCP)

AI assistants can see the printers and the saved labels and print through the [Model Context Protocol](https://modelcontextprotocol.io) at `/mcp`, e.g. `http://labels.local:8013/mcp`: "print a label for the jar of chili, today's date".

The endpoint has no login, like the API. Keep it inside your home network, never make it reachable from the internet.

It needs the packages of `requirements-mcp.txt`, which the Docker image comes with, except on 32-bit ARM, where some of them would have to be built with Rust. Elsewhere:

```bash
pip install -r requirements-mcp.txt
```

## Tools

| Tool | What it does |
|---|---|
| `list_printers` | The printers with their model, status, errors and the label size loaded |
| `list_labels` | The saved labels with their label size, tags and blanks |
| `preview_label` | The image of a saved label with its blanks filled in, or of lines of text and a QR code or barcode |
| `print_label` | Prints a saved label, also once for each of several fillings of its blanks, e.g. 12 name badges from a list |
| `print_text` | Prints lines of text and a QR code or barcode, e.g. of a URL or a Wi-Fi network |

Printing makes paper, so both print tools:

- need the number of labels and print at most `MCP_MAX_COUNT` (default 10) per call, counting each label, not the calls,
- need the printer and the label size the assistant expects, and are refused if the printer has another one loaded,
- return the image of what they printed.

Errors are those of the API, e.g. "EAN-13 takes 12 digits, or 13 with the check digit". Claude Code asks before each print, also in its auto mode, other clients as they are set up.

## Clients

### Claude Code

```bash
claude mcp add --transport http labelburg http://labels.local:8013/mcp
```

### VS Code

In `.vscode/mcp.json`:

```json
{
  "servers": {
    "labelburg": {"type": "http", "url": "http://labels.local:8013/mcp"}
  }
}
```

### Cursor

In `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "labelburg": {"url": "http://labels.local:8013/mcp"}
  }
}
```

### Home Assistant

Add the integration **Model Context Protocol** with the URL of the endpoint, then turn on its tools for the conversation agent of your assistant. Home Assistant uses only tools and no login here.

### claude.ai and Claude Desktop

Their connectors connect from Anthropic's cloud, which cannot reach a server in your home network.

## Host names

The endpoint answers only requests for IP addresses, single-label names (e.g. `printserver`), names ending in `.local`, `.lan` or `.home.arpa`, and those of `MCP_HOSTS`, also in `Origin`. That protects against DNS rebinding: a website that points its own name at your server cannot use it. Add the names of your own domain to `MCP_HOSTS`, e.g. `MCP_HOSTS=labels.example.com`, or `.example.com` for all its subdomains. With `TRUSTED_HOSTS` set, only those of its names are answered too.

Behind a [reverse proxy](reverse-proxy.md) the endpoint is below the path of the label designer, e.g. `https://labels.example.com/labels/mcp`.
