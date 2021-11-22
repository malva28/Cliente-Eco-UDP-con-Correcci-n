#!/usr/bin/python3
import _io
import os
import os.path
import warnings
import re
import socket
from math import ceil
import filecmp

from utils import MyParser, int_to_string_bytearray, NullBuffer
from config import SERVER_ADDRESS, PORT

import jsockets
import sys, threading
import time


class HandshakeException(Exception):
    """Raised when the handshake is not performed correctly"""
    pass


class SocketError(Exception):
    """Raised when something happens to the socket"""
    pass


def identity(x):
    return x


class Client:
    """
    Class for sending stuff to the server.
    This is what the client says.
    """

    def __init__(self, t_max_client):
        """
        Client constructor. Stores the proposal of packet size, the actual
        packet size to use for file transferring and the socket.

        :param t_max_client: the proposed packet size (in bytes)
        """
        self.socket = None
        self.t_max_client = t_max_client
        self.t_max_actual = 0

    def udp_connect(self, server_address: str, port: str):
        """
        Creates and connects a socket to the given server_address and port.
        Uses UDP transfer protocol.
        """

        self.socket = jsockets.socket_udp_connect(server_address, port)
        if self.socket is None:
            raise SocketError("Couldn't create socket for address {} and port {}".format(server_address, port))
        # UDP connection protocol here. Keep socket in s attribute

    def send_client_protocol(self, send_fun, buffer=sys.stdout, **kwargs):
        """
        Wraps a client function adding a nice console message. buffer argument
        controls where this message is printed, default is sys.stdout. Using
        a NullBuffer instead disables printing.
        """
        env = send_fun(**kwargs)
        if env != "":
            print("Cliente envía: ", end='', file=buffer)
            print(env, file=buffer)
        return env

    def send(self, msg: str):
        """
        Encodes a msg and sends it through the socket to the server
        """
        self.socket.send(msg.encode('utf-8'))
        return msg

    def say_hi(self):
        """
        Sends hi to the server
        """

        saludo = "Hola"
        self.send(saludo)
        return saludo

    def send_tmax_client(self):
        """
        Sends the proposed packet size to the server
        """
        t_str = int_to_string_bytearray(self.t_max_client, 5)
        self.socket.send(t_str)
        return str(self.t_max_client)

    def process_file_chunk(self, file_obj: _io.TextIOWrapper):
        return file_obj.read(self.t_max_actual)

    def send_file_chunk(self, file_obj: _io.TextIOWrapper,
                        tmp_file: _io.TextIOWrapper = None):
        """
        Reads and sends a packet of the file file_obj. May write the contents
        (with newlines) in a temp file, if given. By default, it doesn't write
        anything.
        """
        chunk = self.process_file_chunk(file_obj)
        if chunk != "":
            # check this border case. For some reason, sending "" causes the
            # program to loop forever
            if tmp_file:

                print(chunk, end="\n", file=tmp_file)
            self.send(chunk)
        return chunk
        # sends a file with tMaxActual size of packets


class ServerSide:
    """Class for receiving stuff from the server.
    This is what the client hears."""

    def __init__(self, socket: jsockets.socket.socket):
        """
        ServerSide constructor. Receives a socket (possibly the same the client
        is using to hear things), stores the packet size the server can receive
        , the actual size received and the socket.
        """
        self.socket = socket
        self.t_max_server = 0
        self.t_max_actual = 0

    def receive_server_protocol(self, receive_fun):
        """
        Wraps the data received in a pretty server message.
        """
        print("Servidor responde: ", end='')
        resp = receive_fun()
        print()
        return resp

    def receive(self):
        """
        Waits for data to be received, decodes it and returns. Uses an adequate
        buffer size. Used manly for the handshake.
        """
        while True:
            try:
                data = self.socket.recv(1500).decode('utf-8')
                print(data, end='')
                return data
            except:
                data = None
            if not data:
                break

    def check_ok(self):
        """
        Checks that the correct greeting was gotten during the handshake
        """
        proceed_msg = "OK"
        msg = self.receive()
        if msg != proceed_msg:
            print(msg)
            raise HandshakeException("Server didn't greet me properly :'(")
        return msg

    def check_t_max_server(self):
        """
        Checks that the message received corresponds to a valid packet size.
        Used during the handshake
        """
        try:
            msg = self.receive()
            self.t_max_server = int(msg)
            return msg
        except:
            raise HandshakeException("Server messed up the max transfer size! :'(")

    def read_responses(self, arg_dict):
        """
        Function to hear messages indefinitely. arg_dict changes its behavior:
         - ["buffer"]: prints a message with the data received to the buffer
                       given.
         - ["file_out"]: the received data will be stored in a file object,
                         unless NullBuffer is passed
         - ["n_sent_back"]: a counter that increases everytime data is received
                            successfully.
         - ["process_fun"]
        The function terminates when exceptions occur, printing the error when
        it's not due to a socket timeout.
        """
        while True:
            try:
                data = self.socket.recv(self.t_max_actual)
                data = data.decode()
                arg_dict["process_fun"](data)
            except socket.timeout:
                data = None
            except Exception as e:
                print(e.__class__.__name__, e, file=sys.stderr)
                data = None
            if not data:
                break
            print("Servidor responde: ", end='', file=arg_dict["buffer"])
            print(data, end='\n', file=arg_dict["buffer"])
            print(data, end='\n', file=arg_dict["file_out"])
            # a package has been successfully received, the counter increases
            arg_dict["n_sent_back"] += 1


def update_actual_t(client: Client, server: ServerSide):
    """
    Updates the packet size to be sent/received of the client and server side
    upon the agreement after a handshake.
    """
    t_max_actual = min(client.t_max_client, server.t_max_server)
    client.t_max_actual = server.t_max_actual = t_max_actual


class Handshake:
    """
    Class for handling the handshake
    """

    def __init__(self, client: Client, server: ServerSide):
        """
        Handshake constructor. Receives the client and a server side class to
        listen to the server involved in the handshake. Stores a reference to
        the socket as well.
        """
        self.client = client
        self.server = server
        self.socket = client.socket

    def handshake_template(self):
        """
        Returns the start and end message of the handshake.
        """
        return [
            '% Inicio del "handshake" %',
            '% Fin del "handshake" %\n'
            '% A partir de este punto, el cliente puede enviar paquetes arbitrarios. %']

    def handshake_funs(self):
        """
        Functions from the client and server side to call during the handshake
        """
        return [self.client.say_hi,
                self.server.check_ok,
                self.client.send_tmax_client,
                self.server.check_t_max_server]

    def handshake_protocol(self):
        """
        Runs the handshake.
        """
        funs = self.handshake_funs()
        template = self.handshake_template()
        res_list = []

        # greet message
        print(template[0])

        # client-server exchange
        for i in range(len(funs)):
            if i & 1:  # is odd
                res = self.server.receive_server_protocol(funs[i])
            else:
                res = self.client.send_client_protocol(funs[i])
            res_list.append(res)

        # end message
        print(template[-1])






class FileTransfer:
    """
    File Transfer class for sending files to the server
    """
    def __init__(self, client: Client, server_side: ServerSide, filename: str, timeout=3.0, send_delay=0.0,
                 verbose: bool = True, tmp_files: bool = True):
        """
        File transfer constructor

        :param client:      the one sending files
        :param server_side: the one listening and receiving files
        :param filename:    the path to the file to transfer
        :param timeout:     time (seconds) before the connection is closed
        :param send_delay:  delay between each send operation
        :param verbose:     activate/deactivate transfer prints
        :param tmp_files:   enables/disables result writing into files
        """
        self.filename = filename
        self.file_to_send = None

        self.tmp_client_path = ""
        self.tmp_server_path = ""

        self.tmp_client = None
        self.tmp_server = None

        self.client = client
        self.server = server_side

        self.hear_thread = None
        self.timeout = timeout
        self.send_delay = send_delay
        self.verbose = verbose
        self.tmp_files = tmp_files
        self.buffer = None

        # tiene que ser enviado así al thread, pasar solo un int no hará que
        # mute el valor :(
        self.thread_args = {"n_sent_back": 0}

        self.setup()

    def setup(self):
        """
        Initialization of parameters
        """
        self.file_to_send = open(self.filename, "r")
        self.tmp_client_path = os.path.join("tmp", "client_tmp.txt")
        self.tmp_server_path = os.path.join("tmp", "server_tmp.txt")
        self.tmp_client = open(self.tmp_client_path, "w")
        self.tmp_server = open(self.tmp_server_path, "w")
        self.client.socket.settimeout(self.timeout)

        if self.verbose:
            self.buffer = sys.stdout
        else:
            self.buffer = NullBuffer()

        if self.tmp_files:
            self.thread_args["file_out"] = self.tmp_server
        else:
            self.thread_args["file_out"] = NullBuffer()

        self.thread_args["buffer"] = self.buffer
        self.hear_thread = threading.Thread(target=self.server.read_responses, args=(self.thread_args,))

    def run(self):
        """
        Runs the file transfer program. Two threads are used, the main thread
        sends the file chunks to the server and the other thread receives the
        data back to the client
        """
        self.hear_thread.start()

        if self.tmp_files:
            tmp_file_client = self.tmp_client
        else:
            tmp_file_client = None

        data = self.client.send_client_protocol(self.client.send_file_chunk,
                                                buffer=self.buffer,
                                                file_obj=self.file_to_send,
                                                tmp_file=tmp_file_client)
        while data != "":
            time.sleep(self.send_delay)
            data = self.client.send_client_protocol(self.client.send_file_chunk,
                                                    buffer=self.buffer,
                                                    file_obj=self.file_to_send,
                                                    tmp_file=tmp_file_client)

        self.finish()

    def finish(self):
        """
        Waits for the listen thread to be finished, joins it and closes the
        files used
        """
        self.hear_thread.join()
        print("\nArchivo transferido!")

        self.file_to_send.close()
        self.tmp_client.close()
        self.tmp_server.close()

    def statistics(self):
        """
        Retrieves statistics about the program previously run such as total
        bytes sent, packet size, number of packets, tmp files being equal (if
        used), the number of packets lost and the loss percentage
        """
        file_re = r'paquetes[_](?P<total_bytes>\d+)[.]txt'
        r = re.search(file_re, self.filename)
        if r and r.group("total_bytes"):
            total_bytes = int(r.group("total_bytes"))
        else:
            total_bytes = os.path.getsize(self.filename)

        chunk_size = self.client.t_max_actual
        total_chunks = ceil(total_bytes / chunk_size)
        files_equal = filecmp.cmp(self.tmp_client_path, self.tmp_server_path)
        if self.tmp_files and files_equal:
            lost_chunks = 0
            percentage = 0
        else:
            # not good, the packages' order could have been messed up, in which
            # case we can't really say they're "lost"
            # lost_chunks = self.chunk_compare()
            lost_chunks = total_chunks - self.thread_args["n_sent_back"]
            percentage = 100 * lost_chunks / total_chunks
        return {
            "total_bytes": total_bytes,
            "chunk_size": chunk_size,
            "total_chunks": total_chunks,
            "files_equal": files_equal,
            "lost_chunks": lost_chunks,
            "percentage": percentage
        }

    def chunk_compare(self):
        """
        DEPRECATED
        Compares both the client and server tmp files line by line to determine
        which packets are corrupted.
        This function is deprecated because corrupted packets shouldn't be sent
        in the first place, so a simple line count should suffice, but what's
        worse is that two lines being different could be a consequence of UDP
        messing up the send order, but not a packet corruption.
        """
        warnings.warn("Deprecated: don't count missed packages by line per line comparison\n"\
                      "Use a counter for data received instead.", DeprecationWarning)
        lost_chunks = 0
        with open(self.tmp_client_path, "r") as tmp_client:
            with open(self.tmp_server_path, "r") as tmp_server:
                sent_line = tmp_client.readline()
                received_line = tmp_server.readline()
                while sent_line != "":
                    if sent_line != received_line:
                        lost_chunks += 1
                    sent_line = tmp_client.readline()
                    received_line = tmp_server.readline()
        return lost_chunks

    def print_statistics(self):
        """
        Prints the statistics drawn after finishing the transfer
        """
        s = self.statistics()
        template = "=====ESTADÍSTICAS=============\n" \
                   "Total de bytes enviados: \t{}\n" \
                   "Tamaño de un paquete: \t\t{}\n" \
                   "Total paquetes enviados: \t{}\n" \
                   "Número paquetes perdidos: \t{}\n\n".format(s["total_bytes"],
                                                               s["chunk_size"],
                                                               s["total_chunks"],
                                                               s["lost_chunks"])
        if s["lost_chunks"] == 0:
            if s["files_equal"]:
                template += "No se perdieron paquetes durante la transferencia de archivos."
            else:
                template += "Todos los paquetes del archivo fueron transferidos, pero en desorden."
        else:
            template += "Se perdió un {}% de los paquetes durante la transferencia de archivos!". \
                format(round(s["percentage"], 3))

        print(template)
        return s


class Test:
    """
    Class for running one to several tests
    """
    def __init__(self, chunk_size: int, transfer_filename: str, n_reps: int=1, record: bool=False, timeout: float=1.0,
                 send_delay: float=0.0, verbose: bool=True, tmp_files: bool=True):
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
        :param send_delay:         delay (seconds) between each send operation
        :param verbose:            activate/deactivate transfer prints
        :param tmp_files:          enables/disables result writing into files
        """
        self.n_reps = n_reps
        self.record = record
        self.timeout = timeout
        self.chunk_size = chunk_size
        self.transfer_filename = transfer_filename
        self.send_delay = send_delay
        self.verbose = verbose
        self.tmp_files = tmp_files

    def mean(self, a_list):
        """
        Returns the mean of a numeric list
        """
        return sum(a_list) / len(a_list)

    def std(self, a_list):
        """
        Returns the standard deviation of a numeric list
        """
        mean = self.mean(a_list)
        quad_list = [(x - mean) ** 2 for x in a_list]
        return (sum(quad_list) / len(a_list)) ** 0.5

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
        client.socket.settimeout(self.timeout)

        server = ServerSide(client.socket)  # called server for simplicity but is
        # not the server
        handshake = Handshake(client, server)
        handshake.handshake_protocol()

        update_actual_t(client, server)

        transfer = FileTransfer(client, server, args.file, self.timeout, self.send_delay, verbose=self.verbose,
                                tmp_files=self.tmp_files)
        transfer.run()
        statistics = transfer.print_statistics()

        # experiment end
        end = time.time()
        total_time = end - begin
        statistics["time"] = total_time

        print(f"Tiempo total tomado para realizar el handshake y la transferencia de archivos\n",
              f"(Incluyendo el timeout del socket de {self.timeout}s)\n",
              f" y un delay entre envíos de {self.send_delay}s ): {round(total_time, 5)}")
        client.socket.close()
        return statistics

    def repeat_tests(self):
        """
        Repeats the tests the number specified in n_reps. At the end it prints
        the final statistics such as the mean time taken, the mean loss
        percentage and the standard deviations.
        """
        count = self.n_reps
        times = []
        loss = []
        while count > 0:
            statistics = self.run_once()
            times.append(statistics["time"])
            loss.append(statistics["percentage"])
            count-=1

        rep_statistics = {
            "avg_loss": self.mean(loss),
            "std_loss": self.std(loss),
            "avg_time": self.mean(times),
            "std_time": self.std(times)
        }
        for key in ["total_bytes","chunk_size","total_chunks"]:
            rep_statistics[key] = statistics[key]

        return rep_statistics

    def template(self):
        """
        Template for the statistics message for the repeated tests
        """
        return "\n=====ESTADÍSTICAS FINALES=====\n" \
               "Total de bytes enviados: \t{}\n" \
               "Tamaño de un paquete: \t\t{}\n" \
               "Total paquetes enviados: \t{}\n\n" \
               "Número de repeticiones:\t{}\n" \
               "% de pérdida promedio:\t\t{}\n" \
               "desv. estándar de pérdida:\t{}\n" \
               "tiempo tomado promedio:\t\t{}\n" \
               "desv. estándar de tiempo:\t{}\n"

    def run_tests(self):
        """
        Runs the test repetitions, prints the final statistics and stores them
        in a file if record was previously enabled
        """
        if self.n_reps <= 1:
            self.run_once()
        else:
            s = self.repeat_tests()
            num_data = [s["avg_loss"], s["std_loss"],
                        s["avg_time"],s["std_time"]]
            r_num_data = [round(x, 5) for x in num_data]

            print(self.template().format(s["total_bytes"],
                                         s["chunk_size"],
                                         s["total_chunks"],
                                         self.n_reps,
                                         *r_num_data))
            if self.record:
                with open("resultados.csv", "a") as f:
                    print(s["total_bytes"],
                          s["chunk_size"],
                          *r_num_data,
                          sep=",", file=f)


# Press the green button in the gutter to run the script.
if __name__ == '__main__':
    parser = MyParser()
    parser.add_argument("file", help="The file the client will transfer to the server.")
    parser.add_argument("n", help="Max size (in Bytes) of the packages sent by the client.",
                        type=int)

    args = parser.parse_args()

    test = Test(chunk_size=args.n,
                transfer_filename=args.file,
                n_reps=1,
                record=False,
                timeout=0.5,
                send_delay=0.0,
                verbose=True,
                tmp_files=False)

    test.run_tests()
