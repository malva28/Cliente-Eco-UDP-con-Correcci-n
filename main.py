#!/usr/bin/python3
import os
import os.path

from utils import MyParser, FileManager
from config import SERVER_ADDRESS, PORT

import threading
from threading import Event
import time

from client import Client, ServerSide, Handshake, update_actual_t
from go_back import PacketBuffer, Packager, Listener, Sender, Receiver, PacketMaker, GBNTimer, \
    UnreliableConnectionException


class Test:
    """
    Class for running one to several tests
    """

    def __init__(self, chunk_size: int,
                 transfer_filename: str,
                 n_reps: int = 1,
                 record: bool = False,
                 timeout: float = 1.0,
                 reply_timeout=2.0,
                 window_size=10,
                 packet_buffer_size=1000):
        """
        Constructor for Test

        :param chunk_size:         Proposed packet size (in bytes)
        :param transfer_filename:  Path to the file to send
        :param n_reps:             Times the experiment should be repeated
        :param record:             Enable/disable statistics recording. If
                                   enabled, results are appended into
                                   "results.csv"
        :param timeout:            time (seconds) before the connection is
                                   closed
        :param reply_timeout:      time (seconds) before the first unacknowledged
                                   packet triggers the retransmission of the window
        :param window_size:        size of the transmission window in number of packets
        :param packet_buffer_size: size of the packet buffer
        """
        self.n_reps = n_reps
        self.record = record
        self.socket_timeout = timeout
        self.chunk_size = chunk_size
        self.transfer_filename = transfer_filename
        self.reply_timeout = reply_timeout
        self.window_size = window_size
        self.buffer_size = packet_buffer_size

        # reused objects in repetitions
        self.file_manager = None
        self.frdr = None
        self.frec = None
        self.statistics = None

        self.setup()

    def setup(self):
        header_size = 5
        data_size = self.chunk_size - header_size
        self.file_manager = FileManager(self.transfer_filename)
        self.frdr = self.file_manager.make_file_reader(data_size)
        self.frec = self.file_manager.make_file_recorder()

        self.statistics = self.file_manager.make_statistics(total_bytes=os.path.getsize(self.transfer_filename),
                                                            data_packet_size=data_size,
                                                            buffer_size=self.buffer_size,
                                                            window_size=self.window_size,
                                                            timeout=self.reply_timeout,
                                                            n_repetitions=self.n_reps)

    def run_once(self):
        """
        Initializes a new connection, clients and server objects to run the
        program once. It also measures the time taken to do the whole process
        and adds it to the statistics.
        """
        # experiment begin
        begin = time.time()

        client = Client(self.chunk_size)
        client.udp_connect(SERVER_ADDRESS, PORT)

        # socket timeout
        client.socket.settimeout(self.socket_timeout)

        server = ServerSide(client.socket)  # called server for simplicity but is
        # not the server
        handshake = Handshake(client, server)
        handshake.handshake_protocol()

        update_actual_t(client, server)

        self.frdr.open()
        self.frec.open()

        p = PacketMaker(self.chunk_size)
        b = PacketBuffer(self.buffer_size, self.window_size)
        t = GBNTimer(self.reply_timeout)
        event = Event()
        packager = Packager(b, p, self.frdr, event)
        listener = Listener(server)
        sender = Sender(b, t, p, listener, client, event)
        t.set_targets([sender])
        receiver = Receiver(b, p, listener, client, self.frec, event)

        listener_thread = threading.Thread(target=listener.run_listen)
        send_sender_thread = threading.Thread(target=sender.run_send_sender)
        receive_sender_thread = threading.Thread(target=sender.run_listen_sender)
        receiver_thread = threading.Thread(target=receiver.run_receive_and_reply)

        listener_thread.start()
        send_sender_thread.start()
        receive_sender_thread.start()
        receiver_thread.start()

        packager.package_file()
        #print("packager finished!")

        receiver_thread.join()
        #print("Receiver Landed!")

        send_sender_thread.join()
        t.stop()
        #print("Send Sender! Last One!")

        receive_sender_thread.join()
        #print("receive sender joined the fry!")

        self.file_manager.close()

        end = time.time()
        total_time = end - begin
        self.statistics.add_time(total_time)

        print(f"Tiempo total tomado para realizar el handshake y la transferencia de archivos\n",
              f" y un timeout para acknowledgements de {self.reply_timeout}s ): {round(total_time, 5)}")

        if self.file_manager.compare_sent_and_received():
            print("Felicidaeds! Los archivos son iguales c:")
        else:
            raise UnreliableConnectionException("Ermano, revisa la tarea...")

        print("Esperando para cerrar el socket...")
        listener_thread.join()
        print("Terminado!")

        client.socket.close()


    def repeat_tests(self):
        """
        Repeats the tests the number specified in n_reps. At the end it prints
        the final statistics such as the mean time taken, the mean loss
        percentage and the standard deviations.
        """
        count = self.n_reps

        while count > 0:
            print("===== TESTS LEFT: {} =====".format(count))
            self.run_once()
            count -= 1

        self.statistics.print_statistics()
        return

    def run_tests(self):
        if self.n_reps <= 1:
            self.run_once()
        else:
            self.repeat_tests()
            if self.record:
                self.statistics.save()


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    parser = MyParser()
    parser.add_argument("file", help="The file the client will transfer to the server.")
    parser.add_argument("n", help="Max size (in Bytes) of the packages sent by the client.",
                        type=int)

    args = parser.parse_args()

    test = Test(chunk_size=args.n,
                transfer_filename=args.file,
                n_reps=30,
                record=True,
                timeout=5.0,
                reply_timeout=0.5,
                window_size=10,
                packet_buffer_size=20)

    test.run_tests()
