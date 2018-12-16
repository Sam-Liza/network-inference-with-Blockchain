# -*- coding: utf-8 -*-

import random, sys, os
from argparse import ArgumentParser
topo_path = os.path.abspath(os.path.join('..', '..', 'Topology'))
sys.path.insert(0, topo_path)
from transactions_creation import *
from malicious_node import *

def get_servers_id():
    '''
    :return: The ID of the servers defined in the configuration files
    '''
    return ["172.31.19.103:10000", "172.31.19.80:10000", "172.31.16.141:10000", "172.31.21.248:10000", "172.31.19.142:10000",
            "172.31.19.36:10000", "172.31.25.86:10000", "172.31.17.120:10000", "172.31.24.184:10000", "172.31.29.155:10000"]

def configure_server(config_file, unl_from_file = True, stop=False, verbose=False, malicious = 0, num_tx = 10, tree_tx = False):
    '''Uses the parameters defined in the configuration file to create a server and return it.'''
    with open(config_file, 'r') as file:
        obj = json.load(file)
        ip = obj["ip"]
        port = int(obj["port"])
        q = float(obj["quorum"])
        lmc = float(obj["ledger_min_close"])
        lmcl = float(obj["ledger_max_close"])
        tval = {}
        ttimes = {}
        tmp = obj["threshold_values"]
        for t in tmp:
            tval[t] = float(tmp[t])
        tmp = obj["threshold_times"]
        for t in tmp:
            ttimes[t] = float(tmp[t])
        lminc = float(obj["ledger_min_consensus"])
        lmaxc = float(obj["ledger_max_consensus"])
        nrr = False if str(obj["non_responding_routers"])=='False' else True
        if unl_from_file:
            unl = load_unl(obj)
        else:
            id = ip + ":" + str(port)
            unl = random_server_selection(get_servers_id(), id)
        if malicious == 1:
            print '\nSet up Malicious node number 1 !!!\n'
            return malicious_server1(ip, port, q, lmc, lmcl, tval, ttimes, lminc, lmaxc, unl=unl, nrr=nrr,
                                     stop_on_consensus=stop, verbose=verbose, fraudolent_tx = num_tx)
        elif malicious==2:
            return malicious_server2(ip, port, q, lmc, lmcl, tval, ttimes, lminc, lmaxc, unl=unl, nrr=nrr,
                                     stop_on_consensus=stop, verbose=verbose, dropped_tx=num_tx, tree_tx=tree_tx)
        return server(ip, port, q, lmc, lmcl, tval, ttimes, lminc, lmaxc, unl=unl, nrr=nrr,
                      stop_on_consensus=stop, verbose=verbose)

def load_unl(json_obj):
    unl = []
    for n in json_obj["unl"]:
        n_id = n["ip"] + ":" + n["port"]
        unl.append(n_id)
    return unl

def configure_client(config_file):
    '''Uses the parameters defined in the configuration file to create a client and return it.'''
    with open(config_file, 'r') as file:
        obj = json.load(file)
        ip = obj["ip"]
        port = obj["port"]
        validators = []
        for v in obj["validators"]:
            v_id = v["ip"] + ":" + v["port"]
            validators.append(v_id)
        return client(ip, port, validators)

def register_client(c):
    c.ask_client_registration()

def register_observer(s):
    '''Register the provided node as observer of the nodes in its UNL'''
    s.ask_observer_registration()

def random_server_selection(servers, me):
    '''Randomly selects 5 node IDs, different from "me", from the list of servers'''
    selected = set()
    while len(selected) < 5:
        id = random.choice(servers)
        if id not in selected and id != me:
            selected.add(id)
    return selected

def parse_cmd_args():
    parser = ArgumentParser(description = "Runs a single experiment of the simulation")
    parser.add_argument("-t", "--type",
                        dest="type", required = True,
                        help="Type of the experiment to be run."
                             "\n1 = all nodes honest \n2 = Malicious nodes that do not join consensus process"
                             "\n3 = Malicious nodes that join consensus process inserting fraudolent transactions"
                             "\n4 = Malicious nodes that join consensus process dropping honest transactions")
    parser.add_argument("-m", "--malicious", type = bool,
                        dest="malicious", default = False,
                        help="Tells if the server node is malicious. Defaults: False.")
    parser.add_argument("-j", "--join", type = bool,
                        dest="join_consensus", default = True,
                        help = "Tells if the server node joins the consensus process. Defaults: True.")
    parser.add_argument("-ns", "--server_number",
                        dest="server_number", default=1,
                        help="Number of the server.")
    parser.add_argument("-nft", "--fraudolent_transactions",
                        dest="fraudolent_transactions", default=10,
                        help="Number of fraudolent transactions inserted or dropped by malicious nodes."
                             "If omitted, defaults to 10.")
    parser.add_argument("-nht", "--honest_transactions",
                        dest="honest_transactions", default=0,
                        help="Number of honest transactions sent to blockchain nodes. If 0, use default transactions.")
    return  parser.parse_args()


def experiment_one_server(i):
    '''
    Runs server number 'i'. Tis function is run on the remote, blockchain host. Servers from 1 to 6 have each other in their UNL.
    '''
    print '\n----------------- Experiment one ------------------\n'
    server = configure_server('configuration/server' + str(i) + '_config.json', stop=True, verbose=True)
    time.sleep(10 - i * 0.4)  # The last to be run waits less
    register_observer(server)
    time.sleep(10-i*0.4) # The last to be run waits less
    server.start()
    while not server.end():  # Consider one server that for sure has been started (servers[9]!)
        time.sleep(5)
    server.draw_topology()
    server.store_topo_to_file(str(i))

def experiment_one_client(num_htx): #todo rename
    '''
    Configure and run servers for experiment one. Servers from 1 to 6 have each other in their UNL.
    '''
    print '\n----------------- Experiment one ------------------\n'
    # Create transactions and send them to servers
    trans = get_honest_transactions() if num_htx == 0 else get_honest_transactions_tree(num_htx)
    c = configure_client('configuration/client_config.json')
    i= 1
    for sip in c.validators:
        os.system("ssh mininet@" + sip.split(':')[
            0] + " 'cd network-inference-with-BlockchainNEW/Tests/MaliciousNodesDistributedLAN/;"
                 "python run.py --type 1s --server_number " + str(i) + " > /dev/null &'")
        i += 1
    time.sleep(5)
    register_client(c)
    c.send_transactions(trans)
    sys.exit()


def experiment_one_client_interactive(num_htx):
    '''This experiment requires that the function "experiment_one_server" is started on each server by hand.
    This function does not orchestrate the distributed execution!'''
    print '\n----------------- Experiment one ------------------\n'
    # Create transactions and send them to servers
    trans = get_honest_transactions() if num_htx == 0 else get_honest_transactions_tree(num_htx)
    c = configure_client('configuration/client_config.json')
    register_client(c)
    c.send_transactions(trans)
    sys.exit()

def experiment_two_server(i, join = True):
    '''
    Runs server number 'i'. This function is run on the remote, blockchain host. Servers from 1 to 6 have each other in their UNL.
    @param join: True if this server joins the consensus process
    '''
    print '\n----------------- Experiment one ------------------\n'
    server = configure_server('configuration/server' + str(i) + '_config.json', stop=True, verbose=True)
    time.sleep(10 - i * 0.4)  # The last to be run waits less
    register_observer(server)
    time.sleep(10-i*0.4) # The last to be run waits less
    if join: 
        server.start()
        while not server.end():  # Consider one server that for sure has been started (servers[9]!)
            time.sleep(5)
        server.draw_topology()
        server.store_topo_to_file(str(i))
    server.finalize()

def experiment_two_client_interactive(num_htx):
    '''
    Equal to "experiment_one_client_interactive"
    '''
    print '\n----------------- Experiment one ------------------\n'
    # Create transactions and send them to servers
    trans = get_honest_transactions() if num_htx == 0 else get_honest_transactions_tree(num_htx)
    c = configure_client('configuration/client_config.json')
    register_client(c)
    c.send_transactions(trans)
    sys.exit()

def experiment_three_client_interactive(num_htx):
    '''
    Equal to "experiment_one_client_interactive"
    '''
    print '\n----------------- Experiment one ------------------\n'
    # Create transactions and send them to servers
    trans = get_honest_transactions() if num_htx == 0 else get_honest_transactions_tree(num_htx)
    c = configure_client('configuration/client_config.json')
    register_client(c)
    c.send_transactions(trans)
    sys.exit()

def experiment_three_server(i, malicious, num_ftx):
    '''
    Configure and run servers for experiment three. Servers from 1 to 6 have each other in their UNL.    
    :param malicious: True if this server is fraudolent. 
    :param num_ftx: Number of fraudolent transactions (the same!) inserted by each malicious node
    '''
    print '\n----------------- Experiment three ------------------\n'  
    if malicious:
        server = configure_server('configuration/server' + str(i) + '_config.json', stop=True,verbose=True, 
                malicious=1, num_tx=num_ftx) 
    else:
        server = configure_server('configuration/server' + str(i) + '_config.json', stop=True, verbose=True)  
    time.sleep(10 - i * 0.4)  # The last to be run waits less
    register_observer(server)
    time.sleep(10-i*0.4) # The last to be run waits less
    server.start()
    while not server.end():  # Consider one server that for sure has been started (servers[9]!)
        time.sleep(5)
    if not malicious:
        server.draw_topology()
        server.store_topo_to_file(str(i))

def experiment_four(num_htx, num, num_ftx):
    '''
    Configure and run servers for experiment three. Servers from 1 to 6 have each other in their UNL.
    Servers from 7 to 10 have an UNL made of random servers.
    :param num: Number of malicious servers that participate in the consensus process dropping honest transactions.
    Only the servers belonging to the first group (from 1 to 6) are considered. num must be <= |group 1|
    :param num_ftx: Number of honest transactions (the same!) dropped by each malicious node
    '''
    print '\n----------------- Experiment four ------------------\n'
    servers = []
    num_mal = num
    for i in range(1, 7):
        if num_mal > 0:
            if num_htx == 0:
                servers.append(configure_server('configuration/server' + str(i) + '_config.json', stop=True,
                                            verbose=True, malicious=2, num_tx=num_ftx))
            else:
                servers.append(configure_server('configuration/server' + str(i) + '_config.json', stop=True,
                                                verbose=True, malicious=2, num_tx=num_ftx, tree_tx = True))
            num_mal -= 1
        else:
            servers.append(
                configure_server('configuration/server' + str(i) + '_config.json', stop=True, verbose=True))
    for i in range(7, 11):
        servers.append(
            configure_server('configuration/server' + str(i) + '_config.json', unl_from_file=False, stop=True,
                             verbose=True))
    for s in servers:
        register_observer(s)
    # Create transactions and send them to servers
    trans = get_honest_transactions() if num_htx == 0 else get_honest_transactions_tree(num_htx)
    c = configure_client('configuration/client_config.json')
    register_client(c)
    c.send_transactions(trans)
    for s in servers:
        s.start()

if __name__=='__main__':
    args = parse_cmd_args()
    t = args.type
    m = bool(args.malicious)
    j = bool(args.join_consensus)
    ns = int(args.server_number)
    nft = int(args.fraudolent_transactions)
    nht = int(args.honest_transactions)
    if t == '1':
        experiment_one_client(nht)
    if t == '1i':
        experiment_one_client_interactive(nht)
    elif t == '1s':
        experiment_one_server(ns)
    elif t == '2i':
        experiment_two_client_interactive(nht) 
    elif t == '2s':
        experiment_two_server(ns, j)
    elif t == '3i':
        experiment_three_interactive(nht)
    elif t == '3s':
        experiment_three_server(ns, m, nft)
    elif t == '4':
        experiment_four(nht, n, nft)
    else:
        print '\nSpecify an integer type t : 0 < t < 5\n'

