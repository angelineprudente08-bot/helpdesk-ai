"""Builds the automatic acknowledgment message a requester gets right after submitting a ticket."""

def build_acknowledgment(ticket_id, category, priority):
    lines = [f"Your ticket #{ticket_id} has been received."]
    if category and priority:
        lines.append(f"Suggested category: {category}. Suggested priority: {priority}. "
                     "These suggestions will be reviewed by our staff.")
    else:
        lines.append("Our support staff will review its category and priority.")
    lines.append("A technician will be assigned soon. You can reply here with the affected device or service, "
                 "any error message, and when the problem started.")
    lines.append("Please never share passwords or verification codes.")
    return "\n\n".join(lines)