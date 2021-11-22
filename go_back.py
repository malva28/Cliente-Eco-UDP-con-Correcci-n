from threading import Timer, Event
from enum import Enum
from queue import Queue, Empty
from client import Client, ServerSide
from utils import FileReader, FileRecorder
from utils import NullBuffer, FIFOSemaphore
import _io
import sys


class UnreliableConnectionException(Exception):
    """Raised when the received file differs from the sent file"""
    pass


class PacketBuffer:
    """
    Class for storing packets in buffers. Keeps track of the indices of the
    transmission window as well as the buffer indices:
      - base indicates the first sent but unacknowledged package
      - next_seqnum contains the sequence number for the next package that is
        not sent yet
      - next_empty indicates the spot were a new packet can be placed
    the following must hold true:
      - base < next_seqnum (in modulo)
      - next_seqnum - base <= window_size
      - next_empty - base <= buffer_size
    """
    def __init__(self, buffer_size, window_size):
        assert buffer_size > window_size

        self.buffer_size = buffer_size
        self.window_size = window_size

        # initializations of indices
        self.base = 0
        self.next_seqnum = 0
        self.next_empty = 0

        # the packet list
        self.content = [0] * buffer_size

        # the semaphores
        self.mutex = FIFOSemaphore(1)
        self.empty_sem = FIFOSemaphore(buffer_size)
        self.empty_window_sem = FIFOSemaphore(window_size)
        self.full_sem = FIFOSemaphore(0)

    def is_window_transmitting(self):
        return self.base != self.next_seqnum

    def is_within_window(self):
        return ((self.next_seqnum - self.base + self.buffer_size) % self.buffer_size) < self.window_size

    def is_index_within_transmit_window(self, i):
        if self.base <= self.next_seqnum:
            return i>= self.base and i < self.next_seqnum
        else:
            return i >= self.base or i < self.next_seqnum


class PacketDestination(Enum):
    """
    Simple enumerations to distinguish the destination of a packet
      - 1: SENDER
      - 2: RECEIVER
    """
    SENDER = 1
    RECEIVER = 2

    def get_associated_class(self):
        if self.value == 1:
            return PacketForSender
        else:
            return PacketForReceiver


class Packet:
    """
    Base class that represents a packet. Contains the packet data, the sequence
    number for order checking, and the destination, these last two compose the
    header
    """
    def __init__(self, data: str, packet_size: int, seqnum: int, destination: PacketDestination):
        # headers:
        # self.checksum = 0
        self.destination = destination  # 1 char long
        self.seqnum = seqnum  # 4 chars long

        # the data:
        self.data = data

    def corrupt(self, buff: PacketBuffer):
        pass

    def __str__(self):
        return "<packet:{} (header) dest: {}, seqnum: {} (data) {}>".format("", self.destination.name[0], self.seqnum,
                                                                            self.data)


class PacketForSender(Packet):
    """
    Class for packets that are replied to the sender
    """
    def __init__(self, data: str, packet_size: int, seqnum: int):
        super().__init__(data, packet_size, seqnum, PacketDestination.SENDER)

    def corrupt(self, buff: PacketBuffer):
        buff_size = buff.buffer_size
        return 0 > self.seqnum or self.seqnum >= buff_size


class PacketForReceiver(Packet):
    """
    Class for packets that are sent to the receiver
    """
    def __init__(self, data: str, packet_size: int, seqnum: int):
        super().__init__(data, packet_size, seqnum, PacketDestination.RECEIVER)

        # meta data
        self.acknowledged = False
        self.expected_reply = None
        self.is_last_packet = False

        self.history = Queue()

    def corrupt(self, buff: PacketBuffer):
        self.history.put(buff.mutex.acquire())

        expected = buff.content[self.seqnum].data

        #print("releasing mutex")
        buff.mutex.release(additional="PacketForReceiver corrupt")
        return expected != self.data


class PacketMaker:
    """
    Packet factory. It also handles packet encoding, decoding and data filling
    """
    def __init__(self, packet_size):
        self.header_size = 5
        assert packet_size > self.header_size

        self.packet_size = packet_size

    def make_packet_for_receiver(self, data: str, seqnum: int):
        return PacketForReceiver(data, self.packet_size, seqnum)

    def make_packet_for_sender(self, data: str, seqnum: int):
        return PacketForSender(data, self.packet_size, seqnum)

    @staticmethod
    def encode_packet(packet: Packet) -> str:
        packet_str = str(packet.destination.value)
        packet_str += str(packet.seqnum).zfill(4)
        packet_str += packet.data
        return packet_str

    @staticmethod
    def decode_packet(packet_str: str) -> Packet:
        destination = PacketDestination(int(packet_str[0]))
        seqnum = int(packet_str[1:5])
        data = packet_str[5:]
        size = len(packet_str)
        return destination.get_associated_class()(data, size, seqnum)

    def feed_data(self, packet: Packet, data: str):
        packet.data = data[0:self.packet_size - self.header_size]

    def feed_data_from_file(self, packet: Packet, file_obj: _io.TextIOWrapper):
        chunk = file_obj.read(self.packet_size - self.header_size)
        self.feed_data(packet, chunk)


class LastPackageToSendCreatedEvent(Event):
    def __init__(self):
        super().__init__()


class GBNTimer:
    """
    Class for storing the timer for the next retransmission in case of packet
    loss or corruption
    """
    def __init__(self, t_interval: float, targets: list =[]):
        self.t_interval = t_interval
        self.cancelled = False
        self.targets = targets

        # a mutex
        self.mutex = FIFOSemaphore(1)
        self.init_timer()

    def set_targets(self, targets: list):
        self.targets = targets

    def init_timer(self):
        self.timer = Timer(self.t_interval, self.timeout)

    def timeout(self):
        if self.cancelled:
            print("I was cancelled :(")
            return
        #self.mutex.acquire()
        for target in self.targets:
            target.on_timeout()
        #self.mutex.release()

    def start(self):
        # self.mutex.acquire()
        self.timer.cancel()
        self.cancelled = False
        self.init_timer()
        self.timer.start()
        # self.mutex.release()

    def stop(self):
        # self.mutex.acquire()
        self.cancelled = True
        self.timer.cancel()
        # self.mutex.release()


class Packager:
    """
    Class in charge of reading the transfer file, packaging its contents and
    putting them into the packet buffer
    """
    def __init__(self, buffer: PacketBuffer, packet_maker: PacketMaker, file_obj: FileReader, event:LastPackageToSendCreatedEvent):
        self.b = buffer
        self.f = file_obj
        self.packet_maker = packet_maker
        self.last_created_event = event

        self.packaged = 0
        self.history = Queue()

    def put(self, packet: Packet):
        #print("Asking for empty sem")
        r = self.b.empty_sem.acquire(timeout=3.0)
        if not r:
            raise Exception("put empty sem wasn't released in time!")
        else:
            self.history.put(r)
        #print("getting empty sem")
        r=self.b.mutex.acquire(timeout=3.0)
        if not r:
            raise Exception("put mutex wasn't released in time!")
        else:
            self.history.put(r)

        self.b.content[self.b.next_empty] = packet
        self.b.next_empty = (self.b.next_empty + 1) % self.b.buffer_size

        self.packaged += 1

        #print("Giving tickets to full sem...")
        self.b.full_sem.release(additional="packager put")
        #print("releasing mutex")
        self.b.mutex.release(additional="packager put")


    def create_packet_from_file(self) -> PacketForReceiver:
        new_packet = self.packet_maker.make_packet_for_receiver("", self.b.next_empty)
        self.packet_maker.feed_data_from_file(new_packet, self.f)
        return new_packet

    def package_file(self):
        new_packet = self.create_packet_from_file()

        #while not self.f.is_eof():
        while not self.f.next_eof():
            self.put(new_packet)
            new_packet = self.create_packet_from_file()
        #print("setting event...")
        self.last_created_event.set()
        new_packet.is_last_packet = True
        self.put(new_packet)


class Listener:
    """
    Class in charge of receiving the messages from the
    """
    def __init__(self, server: ServerSide):
        # pending packets queue
        self.qreceiver = Queue()
        self.qsender = Queue()
        self.server = server

        self.total = 0

    def process_packet(self, raw_data):
        try:
            packet = PacketMaker.decode_packet(raw_data)
            self.total += 1
            if packet.destination == PacketDestination.SENDER:
                self.qsender.put(packet)
            else:
                self.qreceiver.put(packet)
        except:
            # whoopsie, we got a messed up package...
            return

    def run_listen(self):
        self.server.read_responses(dict(buffer=sys.stdout,
                                        file_out=NullBuffer(),
                                        n_sent_back=0,
                                        process_fun=self.process_packet))


class Sender:
    def __init__(self, buffer: PacketBuffer,
                 timer: GBNTimer,
                 packet_maker: PacketMaker,
                 listener: Listener,
                 client: Client,
                 event: LastPackageToSendCreatedEvent):

        self.b = buffer
        self.packet_maker = packet_maker
        self.listener = listener
        self.client = client
        self.timer = timer
        self.last_created_event = event

        self.acks = 0
        self.timeouts = 0
        self.sends = 0

        self.send_history = Queue()
        self.ack_history = Queue()
        self.timeout_history = Queue()

    def _encode_and_send(self, ind: int):
        packet = self.b.content[ind]
        pkg_str = self.packet_maker.encode_packet(packet)
        #self.client.send(pkg_str)
        self.client.send_client_protocol(self.client.send, sys.stdout, msg=pkg_str)
        return packet.is_last_packet

    def packet_send(self):
        #print("Asking for full sem")
        self.send_history.put(self.b.full_sem.acquire())
        #print("Getting full sem")
        #print("full sem tickets: ", self.b.full_sem._value)
        self.send_history.put(self.b.empty_window_sem.acquire())
        self.send_history.put(self.b.mutex.acquire())
        self.send_history.put(self.timer.mutex.acquire())

        self.sends += 1
        finished = self._encode_and_send(self.b.next_seqnum)
        if self.b.base == self.b.next_seqnum:
            self.timer.start()
        self.b.next_seqnum = (self.b.next_seqnum + 1) % self.b.buffer_size

        self.timer.mutex.release(additional="sender packet_send")
        #self.b.full_sem.release()
        #print("releasing mutex")
        self.b.mutex.release(additional="sender packet_send")
        return finished

    def is_finished(self) -> bool:
        return self.last_created_event.is_set() and self.b.next_seqnum == self.b.next_empty

    def run_send_sender(self):
        finished = False
        #while not self.last_created_event.is_set() or self.b.next_seqnum != self.b.next_empty:
        while not finished:
            #print("Event status: ", self.last_created_event.is_set(),
            #      ", Buffer indices: (next_seqnum: {}, next_empty: {})".format(self.b.next_seqnum, self.b.next_empty))
            finished = self.packet_send()
            #print("is finished? {}".format(finished))
            # print("buffer sems: (empty: {}, full: {})".format(self.b.empty_sem._value, self.b.full_sem._value))
            #print("buffer sems: (empty: {}, full: {})".format(self.b.empty_sem.value(), self.b.full_sem.value()))
        #print("Bye bye, send sender")
        #print()

    def on_timeout(self):
        print("timeout!")
        self.timeout_history.put(self.timer.mutex.acquire())
        self.timeout_history.put(self.b.mutex.acquire())

        self.timeouts += 1

        i = 0
        while i < ((self.b.next_seqnum - self.b.base + self.b.buffer_size) % self.b.buffer_size):
            self._encode_and_send((self.b.base + i) % self.b.buffer_size)
            i += 1

        #print("releasing mutex")
        self.b.mutex.release(additional="sender on_timeout")
        self.timer.mutex.release(additional="sender on_timeout")
        self.timer.start()

    def get_received_pkg(self):
        return self.listener.qsender.get()

    def ack_check(self, packet: Packet):
        #print(packet)
        #print("ack check indices: (base: {}, next seqnum: {})".format(self.b.base, self.b.next_seqnum))
        if not packet.corrupt(self.b) and self.b.is_index_within_transmit_window(packet.seqnum):
            self.acks += 1

            self.ack_history.put(self.b.mutex.acquire())
            self.ack_history.put(self.timer.mutex.acquire())
            while self.b.base != ((packet.seqnum+1)%self.b.buffer_size):
                self.b.content[self.b.base].acknowledged = True
                self.b.base = (self.b.base+1) % self.b.buffer_size
                self.b.empty_window_sem.release(additional="sender ack_check")
                #print("Giving tickets to empty sem...")
                self.b.empty_sem.release(additional="sender ack_check")
            #self.b.base = self.b.base % self.b.buffer_size
            if self.b.base == self.b.next_seqnum:
                #print("Stopping Timer....\n")
                self.timer.stop()
            else:
                self.timer.start()
            self.timer.mutex.release(additional="sender ack_check")
            #print("releasing mutex")
            self.b.mutex.release(additional="sender ack_check")

    def run_listen_sender(self):
        #while True:
        while not self.last_created_event.is_set() or self.b.base != self.b.next_empty:
            new_packet = self.get_received_pkg()
            self.ack_check(new_packet)


class Receiver:
    def __init__(self, buffer: PacketBuffer,
                 packet_maker: PacketMaker,
                 listener: Listener,
                 client: Client,
                 file_recorder: FileRecorder,
                 event: LastPackageToSendCreatedEvent):
        self.expected_seqnum = 0
        self.b = buffer
        self.listener = listener
        self.packet_maker = packet_maker
        self.client = client
        self.last_packet = packet_maker.make_packet_for_sender("ACK", buffer.buffer_size)
        self.file_recorder = file_recorder
        self.last_created_event = event
        #self.resultado = open("resultado.txt","w")

        self.acks = 0

    def update_on_successful_receive(self):
        new_packet = self.packet_maker.make_packet_for_sender("ACK", self.expected_seqnum)
        self.expected_seqnum = (self.expected_seqnum+1) % self.b.buffer_size
        self.last_packet = new_packet

    def valid_received_packet(self, received_pkg: PacketForReceiver):
        return not received_pkg.corrupt(self.b) and received_pkg.seqnum == self.expected_seqnum

    def send_ack(self, received_pkg: PacketForReceiver):
        if self.valid_received_packet(received_pkg):
            self.acks += 1

            self.file_recorder.write(received_pkg.data)
            self.update_on_successful_receive()
            self.check_finish()
        self.client.send(self.packet_maker.encode_packet(self.last_packet))

    def get_received_pkg(self):
        try:
            return self.listener.qreceiver.get(timeout=3.0)
        except Empty:
            return False

    def check_finish(self):
        return self.last_created_event.is_set() and self.expected_seqnum == self.b.next_empty
        #if self.last_created_event.is_set() and self.expected_seqnum == self.b.next_empty:
            #print("=====ZAPATILLA======")
            #self.listener.qreceiver.task_done()
        #    return True

    def run_receive_and_reply(self):
        # receive and reply
        while not self.check_finish():
            received_package = self.get_received_pkg()
            if not received_package:
                continue
            self.send_ack(received_package)
