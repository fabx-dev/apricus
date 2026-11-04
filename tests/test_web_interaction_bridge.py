"""Production async authentication/dispatch and channel retirement."""

from types import SimpleNamespace

from test_gnome_bridge import Invocation, fake_bridge


def test_authenticated_channel_generation_and_single_pending_read(monkeypatch):
    bridge, bus, glib = fake_bridge(monkeypatch, [":1.14"] * 40)
    ready = []
    acks = []
    bridge.interaction = SimpleNamespace(
        integration=ready.append, acknowledge=lambda *args: acks.append(args) or True
    )

    def invoke(method, signature, args, sender=":1.14"):
        invocation = Invocation()
        bridge._call(bus, sender, None, None, method, glib.Variant(signature, args), invocation)
        return invocation

    wrong_sender = invoke("InteractionOpen", "()", (), ":1.99")
    assert "not current" in wrong_sender.error
    generation = invoke("InteractionOpen", "()", ()).reply[0]
    assert len(generation) == 32 and ready == [False]
    first = invoke("InteractionWatch", "(su)", (generation, 0))
    assert first.reply[0] == 1 and ready[-1] is True
    pending = invoke("InteractionWatch", "(su)", (generation, 1))
    assert pending.reply is None and bridge.pending == 0
    bridge.publish_interaction({"state": "ENTERING"})
    assert pending.reply[0] == 2
    assert invoke("InteractionAck", "(sssb)", ("a" * 32, "b" * 32, "INTERACTIVE", True)).reply == (
        False,
    )
    assert not acks
    assert invoke(
        "InteractionAck", "(sssb)", (generation, "b" * 32, "INTERACTIVE", True)
    ).reply == (True,)
    assert acks == [("b" * 32, "INTERACTIVE", True)]
    pending = invoke("InteractionWatch", "(su)", (generation, 2))
    assert invoke("InteractionClose", "(s)", (generation,)).reply == ()
    assert pending.error == "closed" and ready[-1] is False
    assert "expired" in invoke("InteractionWatch", "(su)", (generation, 2)).error
    newer = invoke("InteractionOpen", "()", ()).reply[0]
    assert newer != generation
    assert "expired" in invoke("InteractionClose", "(s)", (generation,)).error
    assert bridge.interaction_generation == newer
