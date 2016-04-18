# Active Geolocator - Data Collection

This is a research project testing active geolocation algorithms.
This website receives data from test programs run on clients all over
the world.  It also explains the project and how you can help.

## What's "active geolocation"?

You're probably already familiar with "[geolocation][]," the process
of figuring out where in the world an Internet host is located.
_Active_ geolocation does this by measuring packet round-trip times
between the host we want to locate and a number of other hosts
("landmarks") in known locations.  Packets travel through the network
at a predictable speed (roughly half the speed of light in vacuum) so
we can, in principle, just convert each round-trip time to a distance,
draw a bunch of circles on a map, and find their intersection.  In
practice, there are complications, but algorithms to deal with them
have been floating around the literature for over a decade: [Octant][]
is probably the best-known, and there are dozens of refinements.

## Why are we doing this?

We want to address two problems with the existing algorithms, and a
problem with the databases that most people use to do _passive_
geolocation (just look the IP address up in a table---much more
convenient, if you're willing to rely on the company that compiled the
database).

The algorithms have mostly only been tested in Europe and the USA,
which have denser and faster data networks than almost anywhere else.
They may be making assumptions that don't hold up in other parts of
the world.  It's also unclear how reliable the algorithms are if the
host to be located is very far away from all the landmarks.  They can
probably get the continent right, but are they accurate _within_ the
continent?

The algorithms are also not designed to handle proxy servers.  If you
are funneling all your network traffic through a proxy, you probably
want to be sure exactly which country the proxy is in; this affects
which laws apply, which online merchants will do business with you,
all sorts of things.  So you might like to apply active geolocation to
find out where the proxy is.  But you can't directly measure
round-trip times _to_ the proxy, only _through_ the proxy and back to
your own machine, which might be very far away.  Abstractly, you would
expect this to come out as all the round-trip times being slower by a
constant factor; is it enough to just subtract that off?  And what is
the right way to estimate the constant factor?

The databases, meanwhile, _seem_ accurate enough, usually, but they
are known to have problems [[1]][] [[2]][], and the companies that
compile them will not share their methodology.  By cross-checking the
databases with self-reported and measured locations, we hope to
quantify how likely the databases are to be in error, and by how much.

## How can you help?

We need people to run our measurement client on computers in known
locations.  We are particularly interested in data collected from
outside Europe and North America, but data from anywhere is helpful.

You should only volunteer if you know the latitude and longitude of
your computer to at least two decimal places of accuracy (this is
roughly 1km of uncertainty), and you are willing to share this
information with us.  _Don't_ look your IP address up in a geolocation
service to get this information.  Looking up the postal address is
usually OK, as long as the post office delivers mail directly to your
building.  Locations measured by a GPS receiver (such as most
smartphones nowadays) are better.

At present, the measurement client can only be run from a Unix command
line, and you need to know how to compile C programs.  Get it from
[the Git repository][ag-repo] and then follow the instructions in the
README.  You are encouraged to read the source code before running the
program.

Running the probe client through VPN proxies is also helpful, but you
need to know the location of the proxy (again, don't just look this up
in a geolocation service) as well as the location of the computer
running the program, and you need to tell the client that a proxy is
in use.  We do not need measurements taken via Tor; we already have
those.  Also, please do _not_ contribute measurements if your Internet
connection is via satellite, because satellite relays impose a large
fixed delay that swamps the time-distance relationship we're looking
for.

If you have a computer in a known location, not within Canada, the
USA, or Western Europe, that runs 24x7 and has a reliable connection
to the Internet, and you're willing to volunteer it for use as a
_landmark_, that is also very helpful.  Being a landmark means that
other people running the measurement client will send TCP SYNs to this
computer, directed to a port you specify; your computer must send
reply packets, either SYN/ACK or RST.  It is more efficient for
everyone involved if you specify a _closed_ port (that is, your
computer sends RSTs).  If you must use an open port, that also works;
the measurement client will always break the connection immediately,
sending no data.  We don't expect this will be a large volume of
traffic but we could be wrong.

Finally, we would welcome contributions of code; we are particularly
interested in improvements to the usability and portability of the
measurement client, alternative methods for making the same
measurements, alternative geolocation algorithms, and ways to present
the data.

## What does the measurement client do?

It downloads a list of IP addresses from this website, and then it
makes several TCP connections to each address, and measures the time
for the connection to resolve.  (If a connection succeeds, it is
immediately closed; no data is sent or received.)  It randomizes the
list and spaces out the connections in time, to minimize impact on the
remote peers.

Once all the measurements are made, the results are transmitted back
to this website along with your computer's IP address and reported
location.  If you took measurements via a proxy, the proxy's IP
address and location are also transmitted.

All of the submitted data is stored either encrypted or on computers
under our physical control.  We may publish the data set in the
future; if we do, it will include your computer's location and AS
number but not its IP address.

[geolocation]: https://www.iplocation.net/
[Octant]: https://www.cs.cornell.edu/~bwong/octant/overview.html
[ag-repo]: https://github.com/zackw/active-geolocator
[[1]]: http://www.sigcomm.org/sites/default/files/ccr/papers/2011/April/1971162-1971171.pdf
[[2]]: http://fusion.net/story/287592/internet-mapping-glitch-kansas-farm/
