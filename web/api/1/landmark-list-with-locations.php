<?php
function error_handler($errno, $errstr, $errfile, $errline) {
    http_response_code(500);
    header("Content-Type: text/plain;charset=utf-8");
    echo "Internal error: $errstr\n";
    exit(1);
}
error_reporting(0);
set_error_handler("error_handler");

include("config.php");

$fp = fopen($LANDMARKS, "r");
$data = [];
while (($row = fgetcsv($fp)) !== FALSE) {
    $data[] = [$row[0], $row[1] + 0,
               $row[2] + 0, $row[3] + 0,
               $row[4] + 0, $row[5] + 0];
}
fclose($fp);

# In addition, we ask the client to ping 127.0.0.1, its apparent
# external IP address, a guess at its gateway address (last component
# of the IPv4 address forced to .1) and this server.  We use TCP port
# 80 for these partially because that's consistent with the main landmarks
# list and partially because the JavaScript client isn't allowed to
# connect to port 7.
$data[] = ["127.0.0.1", 80, 0, 0, 0, 0];

$client = $_SERVER["REMOTE_ADDR"];
# in the unlikely event of an ipv6 address, don't bother; the client
# can't handle them
if (preg_match("/^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$/", $client)) {
    $gw = preg_replace("/\.[0-9]{1,3}$/", ".1", $client);
    $data[] = [$client, 80, 0, 0, 0, 0];
    if ($client !== $gw) {
        $data[] = [$gw, 80, 0, 0, 0, 0];
    }
}
$data[] = [gethostbyname($_SERVER["HTTP_HOST"]), 80, 0, 0, 0, 0];

$blob = json_encode($data);
if ($blob === FALSE) {
    trigger_error(json_last_error_msg(), E_USER_ERROR);
} else {
    header("Content-Type: application/json;charset=utf-8");
    echo $blob;
}
?>