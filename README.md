Lifeline is an experimental project to create a lightweight transparent reconnection WebSocket
drop-in replacement.

Got a JavaScript app that is running on mobile and cannot trust that a vital
WebSocket won't go down the moment you look away for five seconds?

Got a protocol that you can't re-engineer to directly handle reconnections
because, you're writing a MUD or similar thing and it is really very stream
based?

What you need is Lifeline!  We have two components

- LifelineSocket.js, which aspires to present a drop-in replacement for
  WebSocket that your app can use in its place, without caring about any of
  this layer

- lifeline-bouncer.py, a Python app you can run on your server, between
  websockify and your actual service, which will handle Session IDs, reconnecting
  and handling buffered data

Lifeline is shared as an experimental prototype. Features it currently lacking include

- client side buffering, and acks from the server
- i think the server can't deal with partial frames
- i probably just want to increase the frame size width up from 16 bits to 32 bits
- configuration in a file
- server needs to handle the PROXY protocol
