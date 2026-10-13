# MCP Registry entry

`server.json` is the listing for the official MCP Registry (https://registry.modelcontextprotocol.io), namespace
`org.remoteimpact`, proven by the record served at https://remoteimpact.org/.well-known/mcp-registry-auth
(route in `jobboard/urls.py`). The signing key is kept outside the repo at
`/opt/easyclaw/secrets/mcp-registry-key.pem` on the server.

To publish a new version: bump `version` here, then

    PRIVATE_KEY="$(openssl pkey -in mcp-registry-key.pem -noout -text | awk '/priv:/{f=1;next}/pub:/{f=0}f' | tr -d ' :\n')"
    mcp-publisher login http --domain remoteimpact.org --private-key "$PRIVATE_KEY"
    mcp-publisher publish
