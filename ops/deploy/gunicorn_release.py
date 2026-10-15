"""Release code is frozen: HUP is an obsolete and disruptive deployment path."""
def on_starting(server):
    def ignore_reload():
        server.log.warning('Ignored SIGHUP for immutable release; deploy with /opt/easyclaw/deploy.sh')
    server.handle_hup = ignore_reload
