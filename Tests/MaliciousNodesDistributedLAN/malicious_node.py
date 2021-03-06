# -*- coding: utf-8 -*-
from __future__ import division
import threading
from threading import Lock
import socket
import thread
import cPickle as pickle
import rsa
import time, datetime
import os
import sys
import logging
import pdb
from transaction import *
topo_path = os.path.abspath(os.path.join('..', '..', 'Topology'))
blk_path = os.path.abspath(os.path.join('..', '..',  'Blockchain'))
sys.path.insert(0, topo_path)
sys.path.insert(0, blk_path)
from util import *
from graph_tool.all import *
from message import *
from blockchain import *
from proposal import *
from node import *
from transactions_creation import *


class malicious_server1(client):
    """
    A malicious server node of the blockchain network.
    During the establish (aka vote) phase, it inserts also fraudolent transactions and sends them to the other
    blockchain servers.
    """
    def __init__(self, ip_addr, port, q, lmc, lmcl, tval, ttimes, lminc, lmaxc, nrr = True, validators = [], unl = [],
                 stop_on_consensus = False, verbose = False, fraudolent_tx = 10):
        '''

        :param ip_addr:
        :param port:
        :param q:
        :param lmc:
        :param lmcl:
        :param tval:
        :param ttimes:
        :param lminc:
        :param lmaxc:
        :param nrr:
        :param validators:
        :param unl:
        :param stop_on_consensus: True if the node should stop its execution as soon as a consensus is reached.
        :param verbose: If true, logs to file info about completion times of the various phases and on consensus
        :param fraudolent_tx: Number of fraudolent transactions to be inserted by this node during the consensus process

        '''
        super(malicious_server1, self).__init__(ip_addr, port, validators)
        self.observers = [] # Observers are the nodes that inserted this node in their UNL plus the clients of this node
        self.unl = unl
        self.unapproved_tx = {} # Dict st key=transaction, value = number of times transaction rejected by consensus
        self.current_tx = [] #Transactions that are being validated in the current consensus round
        self.in_establish_phase = [] # Number of nodes of UNL that moved on to the establish phase
        self.ssocket = server_socket(ip_addr, port, self)
        self.ssocket.start()
        self.quorum = q
        self.ledger_min_close = lmc
        self.ledger_max_close = lmcl
        self.threshold_values = tval
        self.threshold_times = ttimes
        self.ledger_min_consensus = lminc
        self.ledger_max_consensus = lmaxc
        self.nrr = nrr #True if the blockchain stores also info about non responding routers (A, B, NC and H) TODO Per ora cambia solo la stampa della topologia
        self.open = True
        self.establish = False
        self.accept = False
        self.my_pos = None
        self.unl_pos = {} # Key: node_id, value : [proposals (not yet processed)]
        self.last_pos = {} # key: node_id, value: last_proposal. Also this node is included in this dictionary
        self.unl_ledgers = {} # key: node_id, value (received_all(boolean), ledger)
        for u in unl:
            self.unl_pos[u] = []
            self.last_pos[u] = None
            self.unl_ledgers[u] = (False, None)
        self.last_pos[self.id()] = None
        self.__blockchain = full_blockchain()
        self.__stop_on_consensus = stop_on_consensus
        self.__verbose = verbose
        self.num_fraudolent_tx = fraudolent_tx
        if verbose:
            setup_logger('s' + self.id(), 's' + str(ip_addr) + '_' + str(port) + ".log")
            self.__logger = logging.getLogger('s' + self.id())

    def start(self):
        '''Start this server in a separate thread of execution'''
        thread.start_new_thread(self.run, ())

    def run(self):
        while not self.end():
            self.open_phase()
            self.establish_phase()            
            self.accept_phase()            
        print 'end'

    def open_phase(self):
        '''In the open phase, the node simply collects transactions.
        When one of the three conditions defined in "next_phase()" holds,
        the node passes to the "Establish" phase.'''

        print '\n................................................' \
              '\n\n\n            Open Phase               \n\n\n' \
              '................................................\n'

        def next_phase():
            elapsed = time.time() - start
            c1 = bool(self.unapproved_tx) and elapsed > self.ledger_min_close #Dict evaluates to True if it is non empty
            c2 = elapsed > self.ledger_max_close
            c3 = len(self.in_establish_phase) > (0.5 * len(self.unl))
            return (c1 or c2 or c3)

        self.open = True
        start = time.time()
        sleep_time = self.ledger_min_close / 3  # Avoid spin wait
        while not next_phase():
            time.sleep(sleep_time)
        # Only the transactions already received will be part of the current round of consensus
        for t in self.unapproved_tx:
            self.current_tx.append(t)
        if self.__verbose:
            self.__logger.info('Open phase time ' + str(time.time()-start) + ' , ledger ' + str(self.__blockchain.current_ledger_seq_num()))
        self.open = False



    def establish_phase(self):
        '''The Establish phase is the one in which this node tries to reach the consensus with the other nodes of
        the UNL on the transactions to include in the ledger. Proposals are exchanged until at least a quorum q
        of the nodes in the UNL agrees on the same transaction set.'''

        print '\n................................................' \
              '\n\n\n            Establish Phase               \n\n\n' \
              '................................................\n'

        def next_phase():
            elapsed = time.time() - start
            c1 = elapsed > self.ledger_min_consensus
            c2 = len(self.in_establish_phase) > (0.7 * len(self.unl)) or elapsed > self.ledger_max_consensus
            #c3 = self.same_proposal() >= (self.quorum * len(self.unl))
            return c1 and c2 #and c3

        # Init
        self.establish = True
        start = time.time()
        r = [0] # Round of the proposal
        sleep_time = self.ledger_min_close / 2  # Avoid spin wait
        self.create_my_proposal(r)
        self.send_proposal(r)

        while not next_phase():
            time.sleep(sleep_time)
            #self.__logger.info('Same Proposals: ' + str(self.same_proposal()) + '\nQuorum = ' + str(self.quorum * len(self.unl)))
            if self.new_proposals():
                self.update_last_proposals()
                if self.update_my_proposal(time.time()- start, r):
                    self.send_proposal(r)

        # Phase finalization
        self.in_establish_phase = []
        self.reset_last_pos()
        if self.__verbose:
            self.__logger.info('Establish phase time ' + str(time.time()-start) + ' , ledger ' + str(self.__blockchain.current_ledger_seq_num()))
        self.establish = False

    def same_proposal(self):
        '''Returns the number of nodes in the unl that share the proposal of this node'''
        share = 0
        my_prop = self.last_pos[self.id()]
        for prop in self.last_pos.values():
            if prop!= None and prop.proposer() != self.id():
                if my_prop.tx_set() == prop.tx_set():
                    share += 1
        return share

    def new_proposals(self):
        num = 0
        for u in self.unl_pos:
            num += len(self.unl_pos[u])
        return num > 0

    def update_last_proposals(self):
        '''For each node the list of proposals not yet processed is kept (useful if a proposal is sent via
        multiple messages). This function simply takes the last unprocessed proposal from each node (if any)
        and sets it as last proposal.'''
        for n in self.unl_pos:
            if len(self.unl_pos[n]) != 0:
                max = 0
                last_prop = None
                for prop in self.unl_pos[n]:
                    if prop.complete() and prop.round() > max:
                        max = prop.round()
                        last_prop = prop
                if last_prop is not None: # Could have found only uncomplete proposals..
                    self.last_pos[last_prop.proposer()] = last_prop
                    self.unl_pos[n] = []


    # Ogni mia transazione è testata contro l' ultima proposta di ciascun nodo della unl.
    # Se il numero di nodi in cui compare > quorum allora includi quella transazione nella mia proposta,
    # altrimenti la scarti
    def update_my_proposal(self, elapsed, r):
        '''Returns True if a new proposal was created, False otherwise.'''
        threshold = self.get_threshold_value(elapsed) * len(self.unl)
        votes = {} # key: transaction_id, value : (transaction, number of votes) (All the transactions are considered)
        for prop in self.last_pos.values():
            if prop != None and prop.proposer() != self.id(): #TODO al momento non considero le mie transazioni
                transactions = prop.tx_set().transactions().values()
                for tx in transactions:
                    if tx.id() not in votes:
                        votes[tx.id()] = (tx, 1)
                    else:
                        votes[tx.id()] = (tx, votes[tx.id()][1] + 1)
        self.current_tx = []
        for (t,v) in votes.values():
            if v > threshold:
                self.current_tx.append(t)
        return self.create_my_proposal(r)


    def get_threshold_value(self, elapsed):
        if elapsed < self.threshold_times['init']:
            return self.threshold_values['init']
        if elapsed < self.threshold_times['mid']:
            return self.threshold_values['mid']
        if elapsed < self.threshold_times['late']:
            return self.threshold_values['late']
        return self.threshold_values['stuck']

    def create_my_proposal(self, r):
        '''Creates a proposal from the list of current transactions and adds it to the dict of last positions
            if this proposal is different from the old one. Returns True if a new proposal different from the
            previous one was created, False otherwise. Fraudolent transactions are added.'''
        txset = transaction_set(self.current_tx)
        for t in self.create_fraudolent_tx():
            txset.add_transaction(t)
        new_prop = proposal(self.id(), r[0], txset, self.__blockchain.current_ledger_id())
        if self.last_pos[self.id()] is None or new_prop.tx_set() != self.last_pos[self.id()].tx_set():
            self.last_pos[self.id()] = new_prop
            r[0] = r[0] + 1 # Updates the round
            return True
        return False

    def create_fraudolent_tx(self):
        '''
        Creates a list of fraudolent transactions and returns it
        '''
        num = self.num_fraudolent_tx
        routers = {}
        for i in range(num + 1):
            routers['rF' + str(i)] = topology_node('rF' + str(i), 'R')
        trans = []
        for i in range(num):
            trans.append(transaction(routers['rF' + str(i)], routers['rF' + str(i + 1)]))
        return trans


    def create_proposal_messages(self, transactions, r):
        msgs = []
        num = len(transactions)
        while num > 45:
            tmp = transactions[0:45]
            header = message_header(self.id(), self.signature(), 'id', 1, message_type.proposal)
            txset = transaction_set(tmp)
            payload = message_payload(proposal(self.id(), r[0], txset, self.__blockchain.current_ledger_id(), complete=False))
            msgs.append(message(header, payload))
            transactions = transactions[45:]
            num = len(transactions)
        header = message_header(self.id(), self.signature(), 'id', 0, message_type.proposal)
        txset = transaction_set(transactions)
        payload = message_payload(proposal(self.id(), r[0], txset, self.__blockchain.current_ledger_id()))
        msgs.append(message(header, payload))
        return msgs

    def send_proposal(self, r):
        '''Sends a proposal to the observers. If the proposal carries too many transactions, fragment it.'''
        msgs = self.create_proposal_messages(self.current_tx, r)
        self.send_all(msgs, list=True, servers=self.observers)

    def reset_last_pos(self):
        self.my_pos = self.last_pos[self.id()] #TODO crea proposal nuova, con COPIA transazioni?
        for nid in self.last_pos:
            self.last_pos[nid] = None

    def accept_phase(self):
        print '\n................................................' \
              '\n\n\n             Accept Phase                  \n\n\n' \
              '................................................\n'
        self.accept = True
        start = time.time()
        #pdb.set_trace()
        consensus = self.validate_ledger()
        self.reset_unl_ledgers()
        self.update_unapproved_tx(consensus)
        if self.__verbose:
            self.__logger.info('Accept phase time ' + str(time.time()-start) +
                               ' , ledger ' + str(self.__blockchain.current_ledger_seq_num()))
            self.__logger.info('Consensus Reached: ' + str(consensus))

            #self.__logger.info('Transactions in the Blockchain: ')
            #for t in self.__blockchain.current_ledger().transaction_set():
            #    self.__logger.info(str(t))
        self.accept = False
        if self.__stop_on_consensus:
            if consensus:
                self.stop()


    def validate_ledger(self):
        '''Applies the transactions to the old ledger to generate a new ledger, sends the new ledger to the observers
        and waits to receive the new ledger from the unl. If the new ledger of this node is equal to the ledger
        received from at least 'quorum' nodes of the unl then keep this ledger, otherwise discard this ledger and ask
        the full ledger to the unl.'''

        def wait_unl_ledgers():
            start = time.time()
            elapsed = time.time()
            while elapsed - start < self.ledger_max_consensus and not self.received_all_ledgers():
                time.sleep(self.ledger_max_consensus / 10)
                elapsed = time.time()

        def create_new_ledger():
            tx_list = self.my_pos.tx_set().transactions().values()
            for t in tx_list:  #TODO controlla che my_pos contenga l' ultima proposal
                if t.type() == 'I':
                    new_txset.add_transaction(t)
                else: # Type of transaction is 'Delete'. If dst is None, remove all the tx in which src appears.
                    #pdb.set_trace()
                    dst = t.dst()
                    removed = False
                    if dst is not None:
                        removed = new_txset.remove_transaction(t)
                        tx_list.remove(t)
                        self.current_tx.remove(t)
                        if t in self.unapproved_tx:
                            del self.unapproved_tx[t]
                        #del self.unapproved_tx[t] #Transazione sparisce da Ledger, cancellala qui
                    else:
                        removed = self.remove_all(t.src(), new_txset, tx_list)
                    self.my_pos.tx_set().remove_transaction(t)
                    if not removed:
                        print 'The transaction ' + str(t) + ' was not inserted in previous ledger ' \
                              + str(last_ledger.sequence_number()) + '. Unable to remove it.'
            return full_ledger(last_ledger.sequence_number() + 1, new_txset) if last_ledger is not None \
                else full_ledger(1, new_txset) # Prev pointer added when ledger inserted in the blockchain

        def reached_consensus():
            '''
            Checks whether the consensus was reached
            :return: a pair (bool, ledger), where bool indicates wheterd the consensus was reached and ledger is the
            ledger that results drom consensus process (may be mine or one of my unl nodes). If bool is False, then ledger is None
            '''
            ledgers = []
            for n in self.unl_ledgers:
                if self.unl_ledgers[n][1] is not None:
                    ledgers.append(self.unl_ledgers[n][1])
            # If this ledger is shared among the quorum, keep this
            share = 0
            for l in ledgers:
                if new_ledger == l:
                    share += 1
            if share >= self.quorum * len(self.unl):
                print '\nReached consensus with my ledger\n'
                return (True, new_ledger)
            # Otherwise, if a quorum of nodes share the same ledger, use it as new ledger
            for i1 in range(len(ledgers)-1):
                share = 0
                for i2 in range(i1+1, len(ledgers)):
                    if ledgers[i1] == ledgers[i2]:
                        share += 1
                if share >= self.quorum * len(self.unl):
                    print '\nReached consensus with unl ledger\n'
                    return (True, ledgers[i1])
            print '\nNot reached consensus\n'
            return (False, None)

        #TODO check
        def check_seq_number():
            '''Returns True if the problem on reaching consensus is due to this node working on a different ledger
                (different sequence number) , False otherwise.'''
            ledgers = []
            for n in self.unl_ledgers:
                if self.unl_ledgers[n][1] is not None:
                    ledgers.append(self.unl_ledgers[n][1])
            threshold = len(self.unl) - len(self.unl) * self.quorum
            my_seq_num = 1 if last_ledger is None else last_ledger.sequence_number()
            diff = len(self.unl_ledgers)
            for l in ledgers: #ledgers could not contain all the ledgers of the nodes in the unl, so initialize diff with worst case result
                if l.sequence_number() == my_seq_num:
                    diff -= 1
            return diff > threshold # At most threshold differences are allowed to reach consensus

        last_ledger = self.__blockchain.current_ledger()
        new_txset = transaction_set(last_ledger.transaction_set().transactions().values()) if last_ledger is not None \
            else transaction_set([])
        new_ledger = create_new_ledger()
        self.send_ledger(new_txset.transactions().values(), new_ledger.sequence_number())
        wait_unl_ledgers()
        (consensus, new_ledger) = reached_consensus()
        if not consensus:
            if check_seq_number():
                seq_num = last_ledger.sequence_number() if last_ledger is not None else 1
                (consensus, new_ledger) = self.ask_ledgers(seq_num)
        if consensus:
            self.__blockchain.add_ledger(new_ledger)
        return consensus

    def remove_all(self, node, txset, txlist):
        '''Removes from txset all the transactions in which "node" happears. The transactions have to be removed also
        from the list 'txlist' of transactions undergoing validation process.'''
        removed = False
        for t in txset.transactions().values():
            if node == t.src() or node == t.dst():
                txset.remove_transaction(t)
                txlist.remove(t)
                self.my_pos.tx_set().remove_transaction(t)
                self.current_tx.remove(t)
                if t in self.unapproved_tx:
                    del self.unapproved_tx[t]
                removed = True
        return removed

    def received_all_ledgers(self):
        '''Returns True only if all the nodes in the UNL have sent entirely their ledger. Since a ledger may be
        fragmented to be sent in one message, the dict "unl_ledgers" carries a boolean for each node telling if
        that node has sent all its ledger to this node.'''
        for n in self.unl_ledgers:
            if self.unl_ledgers[n][0] == False:
                return False
        return True

    def send_ledger(self, transactions, seq):
        msgs = self.create_ledger_messages(transactions, seq)
        self.send_all(msgs, list=True, servers=self.observers)

    def create_ledger_messages(self, transactions, seq):
        msgs = []
        num = len(transactions)
        #pdb.set_trace()
        while num > 40:
            tmp = transactions[0:40]
            header = message_header(self.id(), self.signature(), 'id', 1, message_type.ledger)
            txset = transaction_set(tmp)
            payload = message_payload(full_ledger(seq, txset))
            msgs.append(message(header, payload))
            transactions = transactions[40:]
            num = len(transactions)
        header = message_header(self.id(), self.signature(), 'id', 0, message_type.ledger)
        txset = transaction_set(transactions)
        payload = message_payload(full_ledger(seq, txset))
        msgs.append(message(header, payload))
        return msgs

    #TODO: STOP finchè questo nodo non si è riaggiornato
    def ask_ledgers(self, my_seq_num):
        '''Checks which is the minimum sequence number between its own and the sequence numbers of the received ledgers.
            Once found the minimum, asks the unl to get all the ledgers starting from that seq_number up to the last.
            This way, this node returns updated wrt the network.'''
        return (False, None)


    def reset_unl_ledgers(self):
        for u in self.unl_ledgers:
            self.unl_ledgers[u] = (False, None)


    def update_unapproved_tx(self, consensus):
        '''Removes from unapproved_tx the transactions included in the validated ledger and ages the others'''
        if consensus:
            validated_trans_set = self.__blockchain.current_ledger().transaction_set()
            self.print_validated_tx(validated_trans_set) # TODO per debug. O lo rimuovi, o lo metti da qualche altra parte
            #pdb.set_trace()
            for t in self.unapproved_tx.keys():
                if t in validated_trans_set or t.type() == 'D': #TODO pensa se è ok togliere transazioni 'D' senza controllare che siano state applicate
                    del self.unapproved_tx[t]
                    if t.type() == 'D' and t in self.current_tx:
                        self.current_tx.remove(t)
                else:
                    self.unapproved_tx[t] = self.unapproved_tx[t] + 1 # Age the transaction
                    if self.unapproved_tx[t] > 5: #TODO Hard coded, define in config file the maximum age
                        del self.unapproved_tx[t]
        else: # If consensus was not reach, age all the transactions
            for t in self.unapproved_tx.keys():
                self.unapproved_tx[t] = self.unapproved_tx[t] + 1  # Age the transaction
                if self.unapproved_tx[t] > 5:  # TODO Hard coded, define in config file the maximum age
                    del self.unapproved_tx[t]

    def print_validated_tx(self, txset):
        print '\n..............Ledger ' + str(self.__blockchain.current_ledger().sequence_number()) + ' ...................\n'
        for tx in txset.transactions().values():
            print tx
        print '\n....................................................\n'

    def add_node_to_unl(self, node_id):
        if node_id not in self.unl:
            self.unl.append(node_id)
            self.unl_pos[node_id] = []
            self.last_pos[node_id] = None
            self.unl_ledgers[node_id] = (False, None)

    def remove_node_from_unl(self, node_id):
        try:
            self.unl.remove(node_id)
            del self.unl_pos[node_id]
            return True
        except ValueError:
            return False

    # TODO usa lock per strutture dati condivise. Capisci dove possono nascere race conditions.
    # Usare > 1 lock può essere opportuno, altrimenti blocchi thread che lavorano su Strut. Dati diverse!
    def handle_message(self, msg):
        type = msg.type()
        #pdb.set_trace()
        if not self.verify_signature(msg, type):
            return self.create_unsuccess_ack_msg("Unrecognized signature")
        if type == message_type.client_registration:
            ack_msg = self.register_client(msg)
        elif type == message_type.observer_registration:
            ack_msg = self.register_observer(msg)
        # transaction and transaction_set can only be received by a client
        elif type == message_type.transaction:
            ack_msg = self.add_transaction(msg)
        elif type == message_type.transaction_set:
            ack_msg = self.add_transactions(msg)
        elif type == message_type.proposal:
            ack_msg = self.handle_proposal(msg)
        elif type == message_type.ledger:
            ack_msg = self.handle_ledger(msg)
        #TODO puoi inviare richiesta di stop dalla rete?
        #elif type == message_type.end:
        #    ack_msg == self.stop(msg)
        else:
            ack_msg = self.create_unsuccess_ack_msg("Unrecognized type of the request message")
        return ack_msg

    def create_unsuccess_ack_msg(self, text, msg_id = "id"):
        header = message_header(self.id(), self.signature(), msg_id, 0, message_type.ack_failure ) #TODO per id usa un contatore di messaggi?
        payload = message_payload(text)
        ack_msg = message(header, payload)
        return ack_msg

    def create_success_ack_msg(self, content, type = message_type.ack_success, msg_id="id", seq_num = 0 ):
        header = message_header(self.id(), self.signature(), msg_id, seq_num, type ) #TODO per id usa un contatore di messaggi?
        payload = message_payload(content)
        ack_msg = message(header, payload)
        return ack_msg

    def register_client(self, msg):
        id = msg.sender()
        pub_key = msg.content()
        self.add_public_key(id, pub_key)
        #print 'Client ' + id + ' successfully registered'
        return self.create_success_ack_msg(self.public_key())

    def register_observer(self, msg):
        id = msg.sender()
        pub_key = msg.content()
        self.observers.append(id)
        self.add_public_key(id, pub_key)
        #print 'Observer ' + id + ' successfully registered'
        return self.create_success_ack_msg(self.public_key())

    def deregister_observer_with_msg(self, msg):
        '''An observer can be deregistered both if that node asks it with a message
           or if this node takes independently this decision (maybe it is a fraudolent node)'''
        id = msg.id()
        try:
            self.observers.remove(id)
            self.remove_pub_key(id)
            return self.create_success_ack_msg("Deregistration successfully carried out")
        except ValueError:
            return self.create_unsuccess_ack_msg("Node has not been registered yet. Unable to remove it.")

    def deregister_observer(self, id):
        try:
            self.observers.remove(id)
            self.remove_pub_key(id)
            return True
        except ValueError:
            return False

    def deregister_client(self, id):
        try:
            self.remove_pub_key(id)
            return True
        except ValueError:
            return False


    def add_transaction(self, msg):
        tx = msg.content()
        if tx not in self.unapproved_tx:
            self.unapproved_tx[tx] = 0
            return self.create_success_ack_msg("Transaction successfully inserted")
        else:
            return self.create_unsuccess_ack_msg("Transaction already received")

    def add_transactions(self, msg):
        txset = msg.content()
        ins = 0
        tot = len(txset.transactions())
        for tx in txset.transactions().values():
            if tx not in self.unapproved_tx:
                self.unapproved_tx[tx] = 0
                ins += 1

        print 'Inserted ' + str(ins) + ' transactions out of ' + str(tot)
        return self.create_success_ack_msg({'inserted':ins, 'total' : tot})

    def handle_ledger(self, msg):
        print 'received ledger from ' + msg.sender()
        l = msg.content()
        sid = msg.sender()
        all = True if msg.sequence_number() == 0 else False
        if self.unl_ledgers[sid][1] is None:
            self.unl_ledgers[sid] = (all, l)
        else:
            #self.logger().info('Prima: ' + str(self.unl_ledgers[sid][1] ) + ' , ' + str(len(self.unl_ledgers[sid][1].transaction_set().transactions())))  # TODO
            self.unl_ledgers[sid] = (all, self.unl_ledgers[sid][1].merge(l))
            #self.logger().info('Dopo: ' + str(self.unl_ledgers[sid][1] ) + ' , ' + str(len(self.unl_ledgers[sid][1].transaction_set().transactions())))  # TODO
        return self.create_success_ack_msg("Ledger correctly received")

    def handle_proposal(self, msg):
        print 'received proposal from ' + msg.sender()
        prop = msg.content()
        sid = msg.sender()
        #self.logger().info('Prop seq num: ' + str(msg.sequence_number()) +
        #                   ' Proposals received from ' + sid + ' ' + str(len(self.unl_pos[sid])) + ' time: ' + str(datetime.datetime.now()))
        self.update_establish_phase_list(sid)
        if len(self.unl_pos[sid]) == 0:
            self.unl_pos[sid].append(prop)
        else:
            self.manage_unempty_list(prop, sid, msg.sequence_number())
        return self.create_success_ack_msg("Proposal correctly received")

    def update_establish_phase_list(self, id):
        for n in self.in_establish_phase:
            if n == id:
                return # Node already included in the list, nothing to do
        self.in_establish_phase.append(id) # Otherwise include the node in the list

    def manage_unempty_list(self, prop, sid, seq_num):
        '''If the list is not empty, it means that some proposals is already arrived that was not managed yet.
        If it exists a proposal in the list with the same sequence number of the new one, it means that the proposal
        was fragmented when it was sent. In this case, we merge the two proposals into one. If instead the new proposal
        is not the newest, we don't insert it (if some transactions are lost, the next round of consensus will fix it)
        If seq_num = 1 then other transactions have to come to make the proposal complete.
        '''
        r = prop.round()
        max_r = 0
        greater = True
        for p in self.unl_pos[sid]:
            if p.round() == r:
                if seq_num == 1:
                    p.merge(prop, complete = False)
                else:
                    p.merge(prop)
                return
            if prop.round() < p.round():
                return
        self.unl_pos[sid].append(prop)

    def handle_proposal_response(self, response):
        if response.type() == message_type.ack_success:
            #print 'Proposal successfully received from node ' + response.sender()
            pass
        else:
            #print 'Proposal NOT successfully received from node ' + response.sender()
            pass

    def ask_observer_registration(self, servers = []):
        '''Asks the servers in the provided list to register this node as observer.
        If an empty list is provided, the unl list is used instead'''
        msg = self.create_observer_registration_msg()
        if len(servers) == 0:
            servers = self.unl
        for s in servers:
                self.send_to(msg, s)

    def create_observer_registration_msg(self):
        header = message_header(self.id(), self.signature(), 'id', 0, message_type.observer_registration)
        payload = message_payload(self.public_key())
        return message(header, payload)

    def draw_topology(self, collapse=True):
        '''Draws the topology of the current ledger and stores it to file.
           If self.nrr is False, draws only responding routers.
        '''
        # Create data structure for the topology
        lgr = self.__blockchain.current_ledger()
        txset = lgr.transaction_set()
        topo = {} #Key = str(node), values = [str(neighbors)]
        for tx in txset.transactions().values():
            src = str(tx.src())
            dst = str(tx.dst())
            if ((self.nrr) or (src.split(':')[1] == 'R' and dst.split(':')[1] == 'R')):
                if src not in topo:
                    topo[src] = [dst]
                else:
                    topo[src].append(dst)
                if dst not in topo:
                    topo[dst] = []
        g = Graph()
        vprop_name = g.new_vertex_property("string")
        vprop_col = g.new_vertex_property("vector<float>")
        vprop_type = {}  # Key=vertex, value=type
        # Add Vertices to the graph
        added = {}
        for s in topo.keys():
            if s not in added:
                added[s] = g.add_vertex()
            for d in topo[s]:
                if d not in added:
                    added[d] = g.add_vertex()
                g.add_edge(added[s], added[d])
        for v in added:
            (name,type) = v.split(':')
            vprop_name[added[v]] = name
            vprop_type[added[v]] = type
            vprop_col[added[v]] = [0.3, 0.1, 1, 0.9] if type == 'R' else [0.3, 0.4, 0.5, 0.9]
        if collapse:
            removed = g.new_vertex_property("bool")
            removed.a = False
            for v in g.vertices():
                # pdb.set_trace()
                non_resp = {}  # key: 'A|NC|B|H', value = (type of NRR)
                try:
                    for e in v.out_edges():
                        dst_vtx = g.vertex(e.target())
                        type = vprop_type[dst_vtx]
                        if type != 'R':
                            if type not in non_resp:
                                non_resp[type] = (dst_vtx, 1)
                            else:
                                if dst_vtx.out_degree() == 0 and dst_vtx.in_degree() == 1:
                                    removed[dst_vtx] = True
                            vprop_name[dst_vtx] = type
                    non_resp = {}
                    for e in v.in_edges():
                        src_vtx = g.vertex(e.source())
                        type = vprop_type[src_vtx]
                        if type != 'R':
                            if type not in non_resp:
                                non_resp[type] = src_vtx
                            else:
                                if src_vtx.out_degree() == 1 and\
                                (src_vtx.in_degree() == 0 or self.edge_from_v2(src_vtx, v)):
                                    removed[src_vtx] = True
                            vprop_name[src_vtx] = type
                except ValueError:
                    print '\nVertex deleted, skip edge\n'
            g.set_vertex_filter(removed, inverted=True)
        g.vertex_properties["name"] = vprop_name
        g.vertex_properties["color"] = vprop_col
        graph_draw(g, vertex_text=g.vertex_properties["name"], vertex_font_size=18, output_size=(1000, 1000),
                   vertex_fill_color=g.vertex_properties["color"], edge_pen_width=3.5,
                   edge_color=[0, 0, 0, 1], vertex_color=g.vertex_properties["color"], vertex_pen_width=3,
                   output="print_topo.png")

    def edge_from_v2(self, v1, v2):
        'Returns True IFF vertex v1 has only one incoming edge coming from v2'
        if v1.in_degree() == 1:
            for e in v2.in_edges():
                if e.target() == v2:
                    return True
        return False

    def logger(self):
        return self.__logger


    def finalize(self):
        self.ssocket.stop()

class malicious_server2(client):
    """
    A malicious server node of the blockchain network.
    During the establish (aka vote) phase, it drops honest transactions from the UNL proposals.
    """

    def __init__(self, ip_addr, port, q, lmc, lmcl, tval, ttimes, lminc, lmaxc, nrr=True, validators=[], unl=[],
                 stop_on_consensus=False, verbose=False, dropped_tx=10, tree_tx= False):
        '''

        :param ip_addr:
        :param port:
        :param q:
        :param lmc:
        :param lmcl:
        :param tval:
        :param ttimes:
        :param lminc:
        :param lmaxc:
        :param nrr:
        :param validators:
        :param unl:
        :param stop_on_consensus: True if the node should stop its execution as soon as a consensus is reached.
        :param verbose: If true, logs to file info about completion times of the various phases and on consensus
        :param dropped_tx: Number of honest transactions to be removed by this node during the consensus process
        :param tree_tx: True if the inserted transactions are the ones to build a tree
        '''
        super(malicious_server2, self).__init__(ip_addr, port, validators)
        self.observers = []  # Observers are the nodes that inserted this node in their UNL plus the clients of this node
        self.unl = unl
        self.unapproved_tx = {}  # Dict st key=transaction, value = number of times transaction rejected by consensus
        self.current_tx = []  # Transactions that are being validated in the current consensus round
        self.in_establish_phase = []  # Number of nodes of UNL that moved on to the establish phase
        self.ssocket = server_socket(ip_addr, port, self)
        self.ssocket.start()
        self.quorum = q
        self.ledger_min_close = lmc
        self.ledger_max_close = lmcl
        self.threshold_values = tval
        self.threshold_times = ttimes
        self.ledger_min_consensus = lminc
        self.ledger_max_consensus = lmaxc
        self.nrr = nrr  # True if the blockchain stores also info about non responding routers (A, B, NC and H) TODO Per ora cambia solo la stampa della topologia
        self.open = True
        self.establish = False
        self.accept = False
        self.my_pos = None
        self.unl_pos = {}  # Key: node_id, value : [proposals (not yet processed)]
        self.last_pos = {}  # key: node_id, value: last_proposal. Also this node is included in this dictionary
        self.unl_ledgers = {}  # key: node_id, value (received_all(boolean), ledger)
        for u in unl:
            self.unl_pos[u] = []
            self.last_pos[u] = None
            self.unl_ledgers[u] = (False, None)
        self.last_pos[self.id()] = None
        self.__blockchain = full_blockchain()
        self.__stop_on_consensus = stop_on_consensus
        self.__verbose = verbose
        self.num_dropped_tx = dropped_tx
        self.tree_trans = tree_tx
        if verbose:
            setup_logger('s' + self.id(), 's' + str(ip_addr) + '_' + str(port) + ".log")
            self.__logger = logging.getLogger('s' + self.id())

    def start(self):
        '''Start this server in a separate thread of execution'''
        thread.start_new_thread(self.run, ())

    def run(self):
        while not self.end():
            self.open_phase()
            self.establish_phase()
            self.accept_phase()
        print 'end'

    def open_phase(self):
        '''In the open phase, the node simply collects transactions.
        When one of the three conditions defined in "next_phase()" holds,
        the node passes to the "Establish" phase.'''

        print '\n................................................' \
              '\n\n\n            Open Phase               \n\n\n' \
              '................................................\n'

        def next_phase():
            elapsed = time.time() - start
            c1 = bool(
                self.unapproved_tx) and elapsed > self.ledger_min_close  # Dict evaluates to True if it is non empty
            c2 = elapsed > self.ledger_max_close
            c3 = len(self.in_establish_phase) > (0.5 * len(self.unl))
            return (c1 or c2 or c3)

        self.open = True
        start = time.time()
        sleep_time = self.ledger_min_close / 3  # Avoid spin wait
        while not next_phase():
            time.sleep(sleep_time)
        # Only the transactions already received will be part of the current round of consensus
        for t in self.unapproved_tx:
            self.current_tx.append(t)
        if self.__verbose:
            self.__logger.info('Open phase time ' + str(time.time() - start) + ' , ledger ' + str(
                self.__blockchain.current_ledger_seq_num()))
        self.open = False

    def establish_phase(self):
        '''The Establish phase is the one in which this node tries to reach the consensus with the other nodes of
        the UNL on the transactions to include in the ledger. Proposals are exchanged until at least a quorum q
        of the nodes in the UNL agrees on the same transaction set.'''

        print '\n................................................' \
              '\n\n\n            Establish Phase               \n\n\n' \
              '................................................\n'

        def next_phase():
            elapsed = time.time() - start
            c1 = elapsed > self.ledger_min_consensus
            c2 = len(self.in_establish_phase) > (0.7 * len(self.unl)) or elapsed > self.ledger_max_consensus
            # c3 = self.same_proposal() >= (self.quorum * len(self.unl))
            return c1 and c2  # and c3

        # Init
        self.establish = True
        start = time.time()
        r = [0]  # Round of the proposal
        sleep_time = self.ledger_min_close / 2  # Avoid spin wait
        self.create_my_proposal(r)
        self.send_proposal(r)

        while not next_phase():
            time.sleep(sleep_time)
            # self.__logger.info('Same Proposals: ' + str(self.same_proposal()) + '\nQuorum = ' + str(self.quorum * len(self.unl)))
            if self.new_proposals():
                self.update_last_proposals()
                if self.update_my_proposal(time.time() - start, r):
                    self.send_proposal(r)

        # Phase finalization
        self.in_establish_phase = []
        self.reset_last_pos()
        if self.__verbose:
            self.__logger.info('Establish phase time ' + str(time.time() - start) + ' , ledger ' + str(
                self.__blockchain.current_ledger_seq_num()))
        self.establish = False

    def same_proposal(self):
        '''Returns the number of nodes in the unl that share the proposal of this node'''
        share = 0
        my_prop = self.last_pos[self.id()]
        for prop in self.last_pos.values():
            if prop != None and prop.proposer() != self.id():
                if my_prop.tx_set() == prop.tx_set():
                    share += 1
        return share

    def new_proposals(self):
        num = 0
        for u in self.unl_pos:
            num += len(self.unl_pos[u])
        return num > 0

    def update_last_proposals(self):
        '''For each node the list of proposals not yet processed is kept (useful if a proposal is sent via
        multiple messages). This function simply takes the last unprocessed proposal from each node (if any)
        and sets it as last proposal.'''
        for n in self.unl_pos:
            if len(self.unl_pos[n]) != 0:
                max = 0
                last_prop = None
                for prop in self.unl_pos[n]:
                    if prop.complete() and prop.round() > max:
                        max = prop.round()
                        last_prop = prop
                if last_prop is not None:  # Could have found only uncomplete proposals..
                    self.last_pos[last_prop.proposer()] = last_prop
                    self.unl_pos[n] = []

    # Ogni mia transazione è testata contro l' ultima proposta di ciascun nodo della unl.
    # Se il numero di nodi in cui compare > quorum allora includi quella transazione nella mia proposta,
    # altrimenti la scarti
    def update_my_proposal(self, elapsed, r):
        '''Returns True if a new proposal was created, False otherwise.'''
        threshold = self.get_threshold_value(elapsed) * len(self.unl)
        votes = {}  # key: transaction_id, value : (transaction, number of votes) (All the transactions are considered)
        for prop in self.last_pos.values():
            if prop != None and prop.proposer() != self.id():  # TODO al momento non considero le mie transazioni
                transactions = prop.tx_set().transactions().values()
                for tx in transactions:
                    if tx.id() not in votes:
                        votes[tx.id()] = (tx, 1)
                    else:
                        votes[tx.id()] = (tx, votes[tx.id()][1] + 1)
        self.current_tx = []
        for (t, v) in votes.values():
            if v > threshold:
                self.current_tx.append(t)
        return self.create_my_proposal(r)

    def get_threshold_value(self, elapsed):
        if elapsed < self.threshold_times['init']:
            return self.threshold_values['init']
        if elapsed < self.threshold_times['mid']:
            return self.threshold_values['mid']
        if elapsed < self.threshold_times['late']:
            return self.threshold_values['late']
        return self.threshold_values['stuck']

    def create_my_proposal(self, r):
        '''Creates a proposal from the list of current transactions and adds it to the dict of last positions
            if this proposal is different from the old one. Returns True if a new proposal different from the
            previous one was created, False otherwise. Honest transactions are dropped.'''
        txset = transaction_set(self.current_tx)
        trans = self.dropped_tx_list()
        for t in trans:
            txset.remove_transaction(t)
        new_prop = proposal(self.id(), r[0], txset, self.__blockchain.current_ledger_id())
        if self.last_pos[self.id()] is None or new_prop.tx_set() != self.last_pos[self.id()].tx_set():
            self.last_pos[self.id()] = new_prop
            r[0] = r[0] + 1  # Updates the round
            return True
        return False

    def dropped_tx_list(self):
        '''
        Creates a list of transactions to be removed and returns it
        '''
        num = self.num_dropped_tx
        if self.tree_trans:
            return get_honest_transactions_tree(num)
        trans = get_honest_transactions()
        return trans[:num]


        routers = {}
        for i in range(num + 1):
            routers['rF' + str(i)] = topology_node('rF' + str(i), 'R')
        trans = []
        for i in range(num):
            trans.append(transaction(routers['rF' + str(i)], routers['rF' + str(i + 1)]))
        return trans

    def create_proposal_messages(self, transactions, r):
        msgs = []
        num = len(transactions)
        while num > 45:
            tmp = transactions[0:45]
            header = message_header(self.id(), self.signature(), 'id', 1, message_type.proposal)
            txset = transaction_set(tmp)
            payload = message_payload(
                proposal(self.id(), r[0], txset, self.__blockchain.current_ledger_id(), complete=False))
            msgs.append(message(header, payload))
            transactions = transactions[45:]
            num = len(transactions)
        header = message_header(self.id(), self.signature(), 'id', 0, message_type.proposal)
        txset = transaction_set(transactions)
        payload = message_payload(proposal(self.id(), r[0], txset, self.__blockchain.current_ledger_id()))
        msgs.append(message(header, payload))
        return msgs

    def send_proposal(self, r):
        '''Sends a proposal to the observers. If the proposal carries too many transactions, fragment it.'''
        msgs = self.create_proposal_messages(self.current_tx, r)
        self.send_all(msgs, list=True, servers=self.observers)

    def reset_last_pos(self):
        self.my_pos = self.last_pos[self.id()]  # TODO crea proposal nuova, con COPIA transazioni?
        for nid in self.last_pos:
            self.last_pos[nid] = None

    def accept_phase(self):
        print '\n................................................' \
              '\n\n\n             Accept Phase                  \n\n\n' \
              '................................................\n'
        self.accept = True
        start = time.time()
        # pdb.set_trace()
        consensus = self.validate_ledger()
        self.reset_unl_ledgers()
        self.update_unapproved_tx(consensus)
        if self.__verbose:
            self.__logger.info('Accept phase time ' + str(time.time() - start) +
                               ' , ledger ' + str(self.__blockchain.current_ledger_seq_num()))
            self.__logger.info('Consensus Reached: ' + str(consensus))

            # self.__logger.info('Transactions in the Blockchain: ')
            # for t in self.__blockchain.current_ledger().transaction_set():
            #    self.__logger.info(str(t))
        self.accept = False
        if self.__stop_on_consensus:
            if consensus:
                self.stop()

    def validate_ledger(self):
        '''Applies the transactions to the old ledger to generate a new ledger, sends the new ledger to the observers
        and waits to receive the new ledger from the unl. If the new ledger of this node is equal to the ledger
        received from at least 'quorum' nodes of the unl then keep this ledger, otherwise discard this ledger and ask
        the full ledger to the unl.'''

        def wait_unl_ledgers():
            start = time.time()
            elapsed = time.time()
            while elapsed - start < self.ledger_max_consensus and not self.received_all_ledgers():
                time.sleep(self.ledger_max_consensus / 10)
                elapsed = time.time()

        def create_new_ledger():
            tx_list = self.my_pos.tx_set().transactions().values()
            for t in tx_list:  # TODO controlla che my_pos contenga l' ultima proposal
                if t.type() == 'I':
                    new_txset.add_transaction(t)
                else:  # Type of transaction is 'Delete'. If dst is None, remove all the tx in which src appears.
                    # pdb.set_trace()
                    dst = t.dst()
                    removed = False
                    if dst is not None:
                        removed = new_txset.remove_transaction(t)
                        tx_list.remove(t)
                        self.current_tx.remove(t)
                        if t in self.unapproved_tx:
                            del self.unapproved_tx[t]
                        # del self.unapproved_tx[t] #Transazione sparisce da Ledger, cancellala qui
                    else:
                        removed = self.remove_all(t.src(), new_txset, tx_list)
                    self.my_pos.tx_set().remove_transaction(t)
                    if not removed:
                        print 'The transaction ' + str(t) + ' was not inserted in previous ledger ' \
                              + str(last_ledger.sequence_number()) + '. Unable to remove it.'
            return full_ledger(last_ledger.sequence_number() + 1, new_txset) if last_ledger is not None \
                else full_ledger(1, new_txset)  # Prev pointer added when ledger inserted in the blockchain

        def reached_consensus():
            '''
            Checks whether the consensus was reached
            :return: a pair (bool, ledger), where bool indicates wheterd the consensus was reached and ledger is the
            ledger that results drom consensus process (may be mine or one of my unl nodes). If bool is False, then ledger is None
            '''
            ledgers = []
            for n in self.unl_ledgers:
                if self.unl_ledgers[n][1] is not None:
                    ledgers.append(self.unl_ledgers[n][1])
            # If this ledger is shared among the quorum, keep this
            share = 0
            for l in ledgers:
                if new_ledger == l:
                    share += 1
            if share >= self.quorum * len(self.unl):
                print '\nReached consensus with my ledger\n'
                return (True, new_ledger)
            # Otherwise, if a quorum of nodes share the same ledger, use it as new ledger
            for i1 in range(len(ledgers) - 1):
                share = 0
                for i2 in range(i1 + 1, len(ledgers)):
                    if ledgers[i1] == ledgers[i2]:
                        share += 1
                if share >= self.quorum * len(self.unl):
                    print '\nReached consensus with unl ledger\n'
                    return (True, ledgers[i1])
            print '\nNot reached consensus\n'
            return (False, None)

        # TODO check
        def check_seq_number():
            '''Returns True if the problem on reaching consensus is due to this node working on a different ledger
                (different sequence number) , False otherwise.'''
            ledgers = []
            for n in self.unl_ledgers:
                if self.unl_ledgers[n][1] is not None:
                    ledgers.append(self.unl_ledgers[n][1])
            threshold = len(self.unl) - len(self.unl) * self.quorum
            my_seq_num = 1 if last_ledger is None else last_ledger.sequence_number()
            diff = len(self.unl_ledgers)
            for l in ledgers:  # ledgers could not contain all the ledgers of the nodes in the unl, so initialize diff with worst case result
                if l.sequence_number() == my_seq_num:
                    diff -= 1
            return diff > threshold  # At most threshold differences are allowed to reach consensus

        last_ledger = self.__blockchain.current_ledger()
        new_txset = transaction_set(
            last_ledger.transaction_set().transactions().values()) if last_ledger is not None \
            else transaction_set([])
        new_ledger = create_new_ledger()
        self.send_ledger(new_txset.transactions().values(), new_ledger.sequence_number())
        wait_unl_ledgers()
        (consensus, new_ledger) = reached_consensus()
        if not consensus:
            if check_seq_number():
                seq_num = last_ledger.sequence_number() if last_ledger is not None else 1
                (consensus, new_ledger) = self.ask_ledgers(seq_num)
        if consensus:
            self.__blockchain.add_ledger(new_ledger)
        return consensus

    def remove_all(self, node, txset, txlist):
        '''Removes from txset all the transactions in which "node" happears. The transactions have to be removed also
        from the list 'txlist' of transactions undergoing validation process.'''
        removed = False
        for t in txset.transactions().values():
            if node == t.src() or node == t.dst():
                txset.remove_transaction(t)
                txlist.remove(t)
                self.my_pos.tx_set().remove_transaction(t)
                self.current_tx.remove(t)
                if t in self.unapproved_tx:
                    del self.unapproved_tx[t]
                removed = True
        return removed

    def received_all_ledgers(self):
        '''Returns True only if all the nodes in the UNL have sent entirely their ledger. Since a ledger may be
        fragmented to be sent in one message, the dict "unl_ledgers" carries a boolean for each node telling if
        that node has sent all its ledger to this node.'''
        for n in self.unl_ledgers:
            if self.unl_ledgers[n][0] == False:
                return False
        return True

    def send_ledger(self, transactions, seq):
        msgs = self.create_ledger_messages(transactions, seq)
        self.send_all(msgs, list=True, servers=self.observers)

    def create_ledger_messages(self, transactions, seq):
        msgs = []
        num = len(transactions)
        # pdb.set_trace()
        while num > 40:
            tmp = transactions[0:40]
            header = message_header(self.id(), self.signature(), 'id', 1, message_type.ledger)
            txset = transaction_set(tmp)
            payload = message_payload(full_ledger(seq, txset))
            msgs.append(message(header, payload))
            transactions = transactions[40:]
            num = len(transactions)
        header = message_header(self.id(), self.signature(), 'id', 0, message_type.ledger)
        txset = transaction_set(transactions)
        payload = message_payload(full_ledger(seq, txset))
        msgs.append(message(header, payload))
        return msgs

    # TODO: STOP finchè questo nodo non si è riaggiornato
    def ask_ledgers(self, my_seq_num):
        '''Checks which is the minimum sequence number between its own and the sequence numbers of the received ledgers.
            Once found the minimum, asks the unl to get all the ledgers starting from that seq_number up to the last.
            This way, this node returns updated wrt the network.'''
        return (False, None)

    def reset_unl_ledgers(self):
        for u in self.unl_ledgers:
            self.unl_ledgers[u] = (False, None)

    def update_unapproved_tx(self, consensus):
        '''Removes from unapproved_tx the transactions included in the validated ledger and ages the others'''
        if consensus:
            validated_trans_set = self.__blockchain.current_ledger().transaction_set()
            self.print_validated_tx(
                validated_trans_set)  # TODO per debug. O lo rimuovi, o lo metti da qualche altra parte
            # pdb.set_trace()
            for t in self.unapproved_tx.keys():
                if t in validated_trans_set or t.type() == 'D':  # TODO pensa se è ok togliere transazioni 'D' senza controllare che siano state applicate
                    del self.unapproved_tx[t]
                    if t.type() == 'D' and t in self.current_tx:
                        self.current_tx.remove(t)
                else:
                    self.unapproved_tx[t] = self.unapproved_tx[t] + 1  # Age the transaction
                    if self.unapproved_tx[t] > 5:  # TODO Hard coded, define in config file the maximum age
                        del self.unapproved_tx[t]
        else:  # If consensus was not reach, age all the transactions
            for t in self.unapproved_tx.keys():
                self.unapproved_tx[t] = self.unapproved_tx[t] + 1  # Age the transaction
                if self.unapproved_tx[t] > 5:  # TODO Hard coded, define in config file the maximum age
                    del self.unapproved_tx[t]

    def print_validated_tx(self, txset):
        print '\n..............Ledger ' + str(
            self.__blockchain.current_ledger().sequence_number()) + ' ...................\n'
        for tx in txset.transactions().values():
            print tx
        print '\n....................................................\n'

    def add_node_to_unl(self, node_id):
        if node_id not in self.unl:
            self.unl.append(node_id)
            self.unl_pos[node_id] = []
            self.last_pos[node_id] = None
            self.unl_ledgers[node_id] = (False, None)

    def remove_node_from_unl(self, node_id):
        try:
            self.unl.remove(node_id)
            del self.unl_pos[node_id]
            return True
        except ValueError:
            return False

    # TODO usa lock per strutture dati condivise. Capisci dove possono nascere race conditions.
    # Usare > 1 lock può essere opportuno, altrimenti blocchi thread che lavorano su Strut. Dati diverse!
    def handle_message(self, msg):
        type = msg.type()
        # pdb.set_trace()
        if not self.verify_signature(msg, type):
            return self.create_unsuccess_ack_msg("Unrecognized signature")
        if type == message_type.client_registration:
            ack_msg = self.register_client(msg)
        elif type == message_type.observer_registration:
            ack_msg = self.register_observer(msg)
        # transaction and transaction_set can only be received by a client
        elif type == message_type.transaction:
            ack_msg = self.add_transaction(msg)
        elif type == message_type.transaction_set:
            ack_msg = self.add_transactions(msg)
        elif type == message_type.proposal:
            ack_msg = self.handle_proposal(msg)
        elif type == message_type.ledger:
            ack_msg = self.handle_ledger(msg)
        # TODO puoi inviare richiesta di stop dalla rete?
        # elif type == message_type.end:
        #    ack_msg == self.stop(msg)
        else:
            ack_msg = self.create_unsuccess_ack_msg("Unrecognized type of the request message")
        return ack_msg

    def create_unsuccess_ack_msg(self, text, msg_id="id"):
        header = message_header(self.id(), self.signature(), msg_id, 0,
                                message_type.ack_failure)  # TODO per id usa un contatore di messaggi?
        payload = message_payload(text)
        ack_msg = message(header, payload)
        return ack_msg

    def create_success_ack_msg(self, content, type=message_type.ack_success, msg_id="id", seq_num=0):
        header = message_header(self.id(), self.signature(), msg_id, seq_num,
                                type)  # TODO per id usa un contatore di messaggi?
        payload = message_payload(content)
        ack_msg = message(header, payload)
        return ack_msg

    def register_client(self, msg):
        id = msg.sender()
        pub_key = msg.content()
        self.add_public_key(id, pub_key)
        # print 'Client ' + id + ' successfully registered'
        return self.create_success_ack_msg(self.public_key())

    def register_observer(self, msg):
        id = msg.sender()
        pub_key = msg.content()
        self.observers.append(id)
        self.add_public_key(id, pub_key)
        # print 'Observer ' + id + ' successfully registered'
        return self.create_success_ack_msg(self.public_key())

    def deregister_observer_with_msg(self, msg):
        '''An observer can be deregistered both if that node asks it with a message
           or if this node takes independently this decision (maybe it is a fraudolent node)'''
        id = msg.id()
        try:
            self.observers.remove(id)
            self.remove_pub_key(id)
            return self.create_success_ack_msg("Deregistration successfully carried out")
        except ValueError:
            return self.create_unsuccess_ack_msg("Node has not been registered yet. Unable to remove it.")

    def deregister_observer(self, id):
        try:
            self.observers.remove(id)
            self.remove_pub_key(id)
            return True
        except ValueError:
            return False

    def deregister_client(self, id):
        try:
            self.remove_pub_key(id)
            return True
        except ValueError:
            return False

    def add_transaction(self, msg):
        tx = msg.content()
        if tx not in self.unapproved_tx:
            self.unapproved_tx[tx] = 0
            return self.create_success_ack_msg("Transaction successfully inserted")
        else:
            return self.create_unsuccess_ack_msg("Transaction already received")

    def add_transactions(self, msg):
        txset = msg.content()
        ins = 0
        tot = len(txset.transactions())
        for tx in txset.transactions().values():
            if tx not in self.unapproved_tx:
                self.unapproved_tx[tx] = 0
                ins += 1

        print 'Inserted ' + str(ins) + ' transactions out of ' + str(tot)
        return self.create_success_ack_msg({'inserted': ins, 'total': tot})

    def handle_ledger(self, msg):
        print 'received ledger from ' + msg.sender()
        l = msg.content()
        sid = msg.sender()
        all = True if msg.sequence_number() == 0 else False
        if self.unl_ledgers[sid][1] is None:
            self.unl_ledgers[sid] = (all, l)
        else:
            # self.logger().info('Prima: ' + str(self.unl_ledgers[sid][1] ) + ' , ' + str(len(self.unl_ledgers[sid][1].transaction_set().transactions())))  # TODO
            self.unl_ledgers[sid] = (all, self.unl_ledgers[sid][1].merge(l))
            # self.logger().info('Dopo: ' + str(self.unl_ledgers[sid][1] ) + ' , ' + str(len(self.unl_ledgers[sid][1].transaction_set().transactions())))  # TODO
        return self.create_success_ack_msg("Ledger correctly received")

    def handle_proposal(self, msg):
        print 'received proposal from ' + msg.sender()
        prop = msg.content()
        sid = msg.sender()
        # self.logger().info('Prop seq num: ' + str(msg.sequence_number()) +
        #                   ' Proposals received from ' + sid + ' ' + str(len(self.unl_pos[sid])) + ' time: ' + str(datetime.datetime.now()))
        self.update_establish_phase_list(sid)
        if len(self.unl_pos[sid]) == 0:
            self.unl_pos[sid].append(prop)
        else:
            self.manage_unempty_list(prop, sid, msg.sequence_number())
        return self.create_success_ack_msg("Proposal correctly received")

    def update_establish_phase_list(self, id):
        for n in self.in_establish_phase:
            if n == id:
                return  # Node already included in the list, nothing to do
        self.in_establish_phase.append(id)  # Otherwise include the node in the list

    def manage_unempty_list(self, prop, sid, seq_num):
        '''If the list is not empty, it means that some proposals is already arrived that was not managed yet.
        If it exists a proposal in the list with the same sequence number of the new one, it means that the proposal
        was fragmented when it was sent. In this case, we merge the two proposals into one. If instead the new proposal
        is not the newest, we don't insert it (if some transactions are lost, the next round of consensus will fix it)
        If seq_num = 1 then other transactions have to come to make the proposal complete.
        '''
        r = prop.round()
        max_r = 0
        greater = True
        for p in self.unl_pos[sid]:
            if p.round() == r:
                if seq_num == 1:
                    p.merge(prop, complete=False)
                else:
                    p.merge(prop)
                return
            if prop.round() < p.round():
                return
        self.unl_pos[sid].append(prop)

    def handle_proposal_response(self, response):
        if response.type() == message_type.ack_success:
            # print 'Proposal successfully received from node ' + response.sender()
            pass
        else:
            # print 'Proposal NOT successfully received from node ' + response.sender()
            pass

    def ask_observer_registration(self, servers=[]):
        '''Asks the servers in the provided list to register this node as observer.
        If an empty list is provided, the unl list is used instead'''
        msg = self.create_observer_registration_msg()
        if len(servers) == 0:
            servers = self.unl
        for s in servers:
            self.send_to(msg, s)

    def create_observer_registration_msg(self):
        header = message_header(self.id(), self.signature(), 'id', 0, message_type.observer_registration)
        payload = message_payload(self.public_key())
        return message(header, payload)

    def draw_topology(self, collapse=True):
        '''Draws the topology of the current ledger and stores it to file.
           If self.nrr is False, draws only responding routers.
        '''
        # Create data structure for the topology
        lgr = self.__blockchain.current_ledger()
        txset = lgr.transaction_set()
        topo = {}  # Key = str(node), values = [str(neighbors)]
        for tx in txset.transactions().values():
            src = str(tx.src())
            dst = str(tx.dst())
            if ((self.nrr) or (src.split(':')[1] == 'R' and dst.split(':')[1] == 'R')):
                if src not in topo:
                    topo[src] = [dst]
                else:
                    topo[src].append(dst)
                if dst not in topo:
                    topo[dst] = []
        g = Graph()
        vprop_name = g.new_vertex_property("string")
        vprop_col = g.new_vertex_property("vector<float>")
        vprop_type = {}  # Key=vertex, value=type
        # Add Vertices to the graph
        added = {}
        for s in topo.keys():
            if s not in added:
                added[s] = g.add_vertex()
            for d in topo[s]:
                if d not in added:
                    added[d] = g.add_vertex()
                g.add_edge(added[s], added[d])
        for v in added:
            (name, type) = v.split(':')
            vprop_name[added[v]] = name
            vprop_type[added[v]] = type
            vprop_col[added[v]] = [0.3, 0.1, 1, 0.9] if type == 'R' else [0.3, 0.4, 0.5, 0.9]
        if collapse:
            removed = g.new_vertex_property("bool")
            removed.a = False
            for v in g.vertices():
                # pdb.set_trace()
                non_resp = {}  # key: 'A|NC|B|H', value = (type of NRR)
                try:
                    for e in v.out_edges():
                        dst_vtx = g.vertex(e.target())
                        type = vprop_type[dst_vtx]
                        if type != 'R':
                            if type not in non_resp:
                                non_resp[type] = (dst_vtx, 1)
                            else:
                                if dst_vtx.out_degree() == 0 and dst_vtx.in_degree() == 1:
                                    removed[dst_vtx] = True
                            vprop_name[dst_vtx] = type
                    non_resp = {}
                    for e in v.in_edges():
                        src_vtx = g.vertex(e.source())
                        type = vprop_type[src_vtx]
                        if type != 'R':
                            if type not in non_resp:
                                non_resp[type] = src_vtx
                            else:
                                if src_vtx.out_degree() == 1 and \
                                        (src_vtx.in_degree() == 0 or self.edge_from_v2(src_vtx, v)):
                                    removed[src_vtx] = True
                            vprop_name[src_vtx] = type
                except ValueError:
                    print '\nVertex deleted, skip edge\n'
            g.set_vertex_filter(removed, inverted=True)
        g.vertex_properties["name"] = vprop_name
        g.vertex_properties["color"] = vprop_col
        graph_draw(g, vertex_text=g.vertex_properties["name"], vertex_font_size=18, output_size=(1000, 1000),
                   vertex_fill_color=g.vertex_properties["color"], edge_pen_width=3.5,
                   edge_color=[0, 0, 0, 1], vertex_color=g.vertex_properties["color"], vertex_pen_width=3,
                   output="print_topo.png")

    def edge_from_v2(self, v1, v2):
        'Returns True IFF vertex v1 has only one incoming edge coming from v2'
        if v1.in_degree() == 1:
            for e in v2.in_edges():
                if e.target() == v2:
                    return True
        return False

    def logger(self):
        return self.__logger

    # TODO send de-registration message to unl, end message to observers, and stop sockets?
    def finalize(self):
        self.ssocket.stop()
