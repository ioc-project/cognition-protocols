You coordinate a team of specialists (agent ids: microservices, monolith, pragmatist). You
cannot read files outside this directory; everything you need is below. Put every file you write
in ./run/.

TASK FOR THE TEAM: "We are adding a new notification-delivery capability to the product (a notification
service): users can opt in to email / push / SMS notifications for events raised across
several existing subsystems. Your team must develop it, and the team needs one decision on
how to build it that everyone can live with."

Load the concord-initiator skill and follow it to the end. Reach each specialist with
'agentbridge delegate "<message>" --to <agent id> --agent-id driver --endpoint 127.0.0.1:47560'.
Never write a specialist's reply yourself. Report the skill's final output verbatim.
