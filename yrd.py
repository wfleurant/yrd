#!/usr/bin/env python2
from subprocess import Popen, PIPE, check_output
from argh import *
import itertools
import socket
import cjdns
import utils
import json
import time
import sys
import os

YRD_FOLDER = os.environ.get('YRD_FOLDER', '/var/lib/yrd')
YRD_PEERS = os.path.join(YRD_FOLDER, 'peers.d/')
CJDROUTE_CONF = os.environ.get('CJDROUTE_CONF', '/var/lib/yrd/cjdroute.conf')
CJDROUTE_BIN = os.environ.get('CJDROUTE_BIN', 'cjdroute')


@wrap_errors([KeyboardInterrupt, IOError])
def start():
    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)

    p = Popen(['cjdroute'], stdin=PIPE)
    p.communicate(json.dumps(conf))

    c = cjdns.connect(password=conf['admin']['password'])

    for peer in os.listdir(YRD_PEERS):
        yield '[*] adding %r' % peer
        try:
            with open(os.path.join(YRD_PEERS, peer)) as f:
                info = json.load(f)
        except ValueError:
            yield '[-] invalid json'
        else:
            if info['type'] == 'in':
                try:
                    c.addPassword(info['name'], info['password'])
                except KeyError:
                    yield '[-] key error'
            elif info['type'] == 'out':
                addr = utils.dns_resolve(info['addr'])
                c.udpBeginConnection(str(addr), str(info['pk']),
                                     str(info['password']))

    c.disconnect()


@wrap_errors([socket.error, IOError])
def addr():
    'show infos about your node'
    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)
    c = cjdns.connect(password=conf['admin']['password'])

    res = c.nodeForAddr()['result']
    table = list(c.dumpTable())

    yield 'addr\t\t' + res['bestParent']['ip']
    yield 'key\t\t' + res['key']
    yield 'version\t\tv' + str(res['protocolVersion'])
    yield ''
    yield 'links\t\t' + str(res['linkCount'])
    yield 'known routes\t' + str(len(table))

    c.disconnect()


@arg('ip', help='the cjdns ipv6')
@arg('-c', '--count', metavar='count', help='stop after `count` packets')
@arg('-s', '--switch', help='do a switch ping instead of a router ping')
@wrap_errors([KeyboardInterrupt, socket.error, IOError])
def ping(ip, count=0, switch=False):
    'ping a node'
    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)
    c = cjdns.connect(password=conf['admin']['password'])

    ping = c.switchPing if switch else c.routerPing

    for _ in xrange(count) if count else itertools.repeat(None):
        try:
            resp = ping(ip)
        except Exception, e:
            resp = {'error': e}

        if 'error' in resp:
            yield 'Error: %s' % resp['error']
        elif resp['result'] == 'pong' and switch:
            yield 'Reply from %s %dms' % (resp['path'], resp['ms'])
        elif resp['result'] == 'pong':
            yield 'Reply from %s %dms' % (resp['from'], resp['ms'])
        elif resp['result'] == 'timeout':
            yield 'Timeout from %s after %dms' % (ip, resp['ms'])

        time.sleep(1)

    c.disconnect()


@arg('-f', '--verify', help='verify the selected route')
@wrap_errors([KeyboardInterrupt, socket.error])
def tr(target, verify=False):
    'traceroute a node'
    c = cjdns.connect()

    if verify:
        res = c.nodeForAddr()['result']
        lastHop = res['bestParent']['ip']

        yield lastHop

        while True:
            x = c.nextHop(target, lastHop)
            # TODO: implement
            return
    else:
        found = 0
        for route in c.dumpTable():
            if route['ip'] != target:
                continue

            found += 1

            yield '[+] found route #%d' % found
            # TODO: show the route

    c.disconnect()


@arg('-f', '--follow', help='show new nodes while they\'re discovered')
@wrap_errors([KeyboardInterrupt, socket.error])
def r(follow=False):
    'access the nodestore'
    c = cjdns.connect()

    known = []
    FMT = '%s %s  v%d %11d %7d'

    while True:
        for node in c.dumpTable():
            if node['ip'] not in known:
                yield FMT % (node['ip'], node['path'], node['version'],
                             node['link'], node['time'])

                known.append(node['ip'])

        if not follow:
            break

        time.sleep(3)

    c.disconnect()


@arg('-n', '--neighbours', help='show neighbours peers')
@arg('-b', '--bw', help='monitor bandwidth')
@aliases('neighbours')
@wrap_errors([socket.error, IOError, KeyboardInterrupt])
def n(neighbours=False, bw=False):
    'shows your neighbours'
    c = cjdns.connect()

    STAT_FORMAT = '%s %19s  v%-2d  %9s %9s  %12s  %d/%d/%d  '
    nodestore = list(c.dumpTable())

    connections = {}

    try:
        for peer in os.listdir(YRD_PEERS):
            with open(os.path.join(YRD_PEERS, peer)) as f:
                info = json.load(f)
                try:
                    connections[info['pk']] = str(info['name'])
                except KeyError:
                    pass
    except OSError:
        pass

    stats = {}

    while True:
        if bw:
            sys.stderr.write('\x1b[2J\x1b[H')

        for peer in c.peerStats():
            result = c.nodeForAddr(peer.ip)['result']

            route = utils.grep_ns(nodestore, peer.ip)
            path = utils.get_path(route)

            setattr(peer, 'path', path)

            if bw:
                entry = stats.get(peer.ip, (peer.bytesIn, peer.bytesOut))
                stats[peer.ip] = (peer.bytesIn, peer.bytesOut)
                entry = [
                    utils.speed(peer.bytesIn - entry[0]),
                    utils.speed(peer.bytesOut - entry[1])
                ]
            else:
                entry = (peer.bytesIn, peer.bytesOut)

            line = STAT_FORMAT % (peer.ip, peer.path, peer.version,
                                  entry[0], entry[1], peer.state,
                                  peer.duplicates, peer.lostPackets,
                                  peer.receivedOutOfRange)

            if hasattr(peer, 'user'):
                line += repr(peer.user)
            elif peer.publicKey in connections:
                line += repr(connections[peer.publicKey])

            yield line

            if neighbours:
                for i in range(result['linkCount']):
                    link = c.getLink(peer.ip, i)

                    if link and 'child' in link['result']:
                        child = link['result']['child']
                        route = utils.grep_ns(nodestore, child)

                        version = utils.get_version(route)
                        path = utils.get_path(route)

                        yield '   %s   %s  v%s' % (child, path, version)
                    else:
                        yield '   -'

        if not bw:
            break

        time.sleep(1)

    c.disconnect()


@arg('-t', '--trace', help='')
@wrap_errors([IOError])
def uplinks(ip, trace=False):
    'show uplinks of a node'
    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)
    c = cjdns.connect(password=conf['admin']['password'])
    nodestore = list(c.dumpTable())

    result = c.nodeForAddr(ip)['result']
    for i in range(result['linkCount']):
        link = c.getLink(ip, i)

        if link and 'child' in link['result']:
            child = link['result']['child']
            route = utils.grep_ns(nodestore, child)

            version = utils.get_version(route)
            path = utils.get_path(route)

            yield '%s   %s  v%d' % (child, path, version)
        else:
            yield('-')

    c.disconnect()


@arg('-b', '--hub', help='query hub.hyperboria.net')
@wrap_errors([socket.error, KeyboardInterrupt])
def whois(ip, hub=False):
    'asks the remote server for whois information'
    if hub:
        import requests
        j = requests.get('http://api.hyperboria.net/v0/node/info.json?ip=%s' % ip).json
        if not type(j) is list:
            j = j()

        def show(path, x):
            if type(x) is dict:
                for a, b in x.items():
                    for line in show('%s/%s' % (path, a), b):
                        yield line
            elif type(x) is list:
                for a, b in enumerate(x):
                    for line in show('%s/%s' % (path, a), b):
                        yield line
            else:
                yield ('%s: %s' % (path, x)).lstrip('/')

        yield '%% %s hub.hyperboria.net whois information' % ip
        yield '%'

        for line in show('', j):
            yield line
    else:
        yield '%% %s (direct connect) whois information' % ip
        yield '%'

        c = socket.create_connection((ip, 43))
        c.send("%s\r\n" % ip)
        while True:
            data = c.recv(4096)
            if not data:
                break
            for line in data.split('\n'):
                line = line.rstrip()
                yield repr(line)[1:-1]
        c.close()


@named('auth')
@arg('password', nargs='?', help='Set peering password')
@arg('-l', '--live', help='Don\'t write to disk')
@arg('-c', '--cjdroute', help='Show cjdroute output only')
@arg('-y', '--yrd', help='Show yrd output only')
@arg('-j', '--json', dest='json_output', help='Show json output only')
@wrap_errors([socket.error, IOError])
def peer_auth(name, password, live=False, cjdroute=False, yrd=False, json_output=False):
    'add a password for inbound connections'

    if '/' in name:
        yield 'nope'
        exit(1)

    path = os.path.join(YRD_PEERS, name)
    if os.path.exists(path):
        with open(path) as f:
            password = json.load(f)['password']
    else:
        if not password:
            password = utils.generate_key(31)

        info = {
            'type': 'in',
            'name': name,
            'password': password
        }

        if not live:
            with open(path, 'w') as f:
                f.write(json.dumps(info))

    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)
    c = cjdns.connect(password=conf['admin']['password'])
    c.addPassword(name, password)
    c.disconnect()

    publicKey = conf['publicKey']
    port = conf['interfaces']['UDPInterface'][0]['bind'].split(':')[1]

    if json_output:
        yield json.dumps({'ip': utils.get_ip(), 'port': port,
                         'pk': publicKey, 'password': password})
    else:
        if (not cjdroute and not yrd) or cjdroute:
            yield utils.to_credstr(utils.get_ip(), port, publicKey, password)
        if not cjdroute and not yrd:
            yield ''
        if (not cjdroute and not yrd) or yrd:
            yield 'yrd peer add namehere %s:%s %s %s' % (utils.get_ip(), port,
                                                     publicKey, password)


@named('ls')
@wrap_errors([IOError])
def peer_ls():
    'list passwords for inbound connections'
    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)
    c = cjdns.connect(password=conf['admin']['password'])
    for user in c.listPasswords()['users']:
        yield user
    c.disconnect()


@arg('name', help='the peers name')
@arg('addr', help='the peers address (ip:port)')
@arg('pk', help='the peers public key')
@arg('password', nargs='?', help='the password')
@arg('-l', '--live', help='Don\'t write to disk')
@named('add')
@wrap_errors([IOError])
def peer_add(name, addr, pk, password, live=False):
    'add an outbound connection'
    if '/' in name:
        yield 'nope'
        exit(1)

    if not password:
        password = raw_input('Password: ')

    path = os.path.join(YRD_PEERS, name)

    info = {
        'type': 'out',
        'name': name,
        'addr': addr,
        'pk': pk,
        'password': password
    }

    if not live:
        with open(path, 'w') as f:
            f.write(json.dumps(info))

    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)

    addr = utils.dns_resolve(addr)

    c = cjdns.connect(password=conf['admin']['password'])
    c.udpBeginConnection(addr, pk, password)
    c.disconnect()


@named('remove')
@wrap_errors([IOError])
def peer_remove(user):
    'unpeer a node'
    if '/' in user:
        yield 'nope'
        exit(1)

    path = os.path.join(YRD_PEERS, user)
    if os.path.exists(path):
        os.unlink(path)
    else:
        yield 'user not found'

    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)
    c = cjdns.connect(password=conf['admin']['password'])
    c.removePassword(user)
    c.disconnect()


@named('get')
def nf_get(*trackers):
    'query public peers'
    import nf
    for tracker in trackers:
        for peer in nf.request_peers(tracker):
            yield peer.credentialstr()


@named('peer')
def nf_peer(*trackers):
    'connect to public peers'
    import nf
    for tracker in trackers:
        for peer in nf.request_peers(tracker):
            addr = '%s:%d' % (peer.ip, peer.port)
            peer_add(peer.ip, addr, peer.publicKey, peer.password)
            yield '[+] peered with %s' % addr


@arg('tracker', help='the tracker you want to announce on')
@arg('password', help='the password you want to share')
@arg('-1', '--oneshot', help='if you want to announce per cronjob')
@arg('contact', nargs='?', help='if you want to allow contact')
@named('announce')
@wrap_errors([KeyboardInterrupt, IOError])
def nf_announce(tracker, password, contact, oneshot=False):
    'announce yourself as public peer'
    import nf

    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)

    addr = conf['interfaces']['UDPInterface'][0]['bind']
    peer = {
        'port': int(addr.split(':')[1]),
        'publicKey': conf['publicKey'],
        'password': password
    }

    if contact:
        peer['contact'] = contact

    while True:
        try:
            if nf.announce(tracker, **peer):
                yield '[+] Told the tracker we\'re here'
        except (IOError, ValueError), e:
            yield '[-] %s' % e

        if oneshot:
            break

        time.sleep(120)


@named('seek')
def wrbt_seek():
    'create a peering request'
    import wrbt
    url, pk = wrbt.request()
    yield 'Import offer: yrd wrbt import "%s" <offer>' % pk
    yield url


@named('confirm')
def wrbt_confirm(name, url):
    'confirm a peering request'
    import wrbt
    request = wrbt.decode(url)

    conf = utils.load_conf(CJDROUTE_CONF, CJDROUTE_BIN)

    host = utils.get_ip()
    port = conf['interfaces']['UDPInterface'][0]['bind'].split(':')[1]
    publicKey = conf['publicKey']
    password = utils.generate_key(31)

    # TODO: authorize

    yield wrbt.confirm(request, (host, port), publicKey, password)


@arg('-d', '--display', help='display only')
@named('import')
def wrbt_import(pk, url, display=False):
    'import a peering offer'
    import wrbt
    offer = wrbt.decode(url)
    msg = wrbt.decrypt(pk, offer)

    if display:
        yield msg
    else:
        for addr, creds in msg['credentials'].items():
            name = addr.split(':')[0]
            peer_add(name, addr, creds['publicKey'], creds['password'])
            yield '[+] peered with %s' % addr


parser = ArghParser()
parser.add_commands([start, addr, n, ping, tr, r, uplinks, whois])
parser.add_commands([peer_auth, peer_add, peer_ls, peer_remove],
                    namespace='peer', title='ctrl peers')
parser.add_commands([nf_get, nf_peer, nf_announce],
                    namespace='nf', title='ctrl inet auto-peering')
parser.add_commands([wrbt_seek, wrbt_confirm, wrbt_import],
                    namespace='wrbt', title='wrbt implementation')

if __name__ == '__main__':
    dispatch(parser)
