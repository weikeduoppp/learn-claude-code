#!/usr/bin/env python3
"""Safe in-repo demo of the s16 shutdown protocol (NOT a system shutdown)."""
import sys
sys.path.insert(0, "/mnt/d/code/ss/learn-claude-code/s16_team_protocols")
import code as s16

print("=== s16 shutdown protocol demo ===")

# 1) Lead creates a shutdown request
req_id = s16.new_request_id()
s16.pending_requests[req_id] = s16.ProtocolState(
    request_id=req_id, type="shutdown",
    sender="lead", target="teammate1",
    status="pending", payload="")
s16.BUS.send("lead", "teammate1", "Please shut down gracefully.",
             "shutdown_request", {"request_id": req_id})

# 2) Teammate dispatches its inbox (mirrors handle_inbox_message)
inbox = s16.BUS.read_inbox("teammate1")
stop = False
for msg in inbox:
    if msg.get("type") == "shutdown_request":
        rid = msg.get("metadata", {}).get("request_id", "")
        s16.BUS.send("teammate1", "lead", "Shutting down gracefully.",
                     "shutdown_response",
                     {"request_id": rid, "approve": True})
        stop = True

# 3) Lead consumes inbox -> routes protocol via match_response
msgs = s16.consume_lead_inbox(route_protocol=True)
state = s16.pending_requests[req_id]

print("\nteammate should_stop:", stop)
print("lead inbox types:", [m["type"] for m in msgs])
print("protocol state:", state.status)
print("RESULT:", "PASS" if state.status == "approved" and stop else "FAIL")
