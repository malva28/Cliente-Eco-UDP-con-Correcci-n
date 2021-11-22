import argparse
import os.path
import sys
import io
import _io
import filecmp
from collections import OrderedDict
from math import ceil
import csv
from queue import Queue, Empty


def false():
    return False

class MyParser(argparse.ArgumentParser):
    def error(self, msg):
        sys.stderr.write('error: {}\n'.format(msg))
        self.print_help(sys.stderr)
        sys.exit(1)


def int_to_string_bytearray(a: int, digit: int = 5) -> bytearray:
    """
    para convertir un integer a un string como bytearray de al menos digit dígitos
    """
    return f'{a:d}'.zfill(digit).encode('utf-8')


def string_bytearray_to_int(b: bytearray) -> int:
    """
    Para decodificar un string bytearray a un integer
    """
    return int(b.decode('utf-8'))


class NullBuffer(io.TextIOWrapper):
    def __init__(self):
        pass

    def write(self, *args, **kwargs):
        pass


class FileManager:
    def __init__(self, filename):
        self.filename = filename
        self.basename = ""
        self.record_filename = ""
        self.statistics_filename = ""

        self.file_reader = None
        self.file_recorder = None

        self.setup()

    def setup(self):
        self.basename = os.path.basename(self.filename)
        self.record_filename = os.path.join("received", "rcv_" + self.basename)
        base_basename = os.path.splitext(self.basename)[0]
        self.statistics_filename = os.path.join("results", "res_" + base_basename + ".csv")

    def make_file_reader(self, chunk_size: int):
        self.file_reader = FileReader(self.filename, chunk_size)
        return self.file_reader

    def make_file_recorder(self):
        self.file_recorder = FileRecorder(self.record_filename)
        return self.file_recorder

    def make_statistics(self, **kwargs):
        return Statistics(self.statistics_filename, **kwargs)

    def close(self):
        self.file_reader.close() if self.file_reader else None
        self.file_recorder.close() if self.file_recorder else None

    def compare_sent_and_received(self):
        return filecmp.cmp(self.filename, self.record_filename)


class FileReader:
    def __init__(self, filename: str, chunk_size: int):
        self.filename = filename
        self.file_obj = None
        self.last_read = None
        self.next_read = None
        self.chunk_size = chunk_size

    def open(self):
        self.file_obj = open(self.filename, "r")
        self.last_read = None
        self.read(1)

    def read(self, size_bytes: int):
        current = self.next_read
        # chunk_read = self.file_obj.read(size_bytes)
        chunk_read = self.file_obj.read(self.chunk_size)
        self.last_read = chunk_read
        self.next_read = chunk_read
        #return chunk_read
        return current

    def is_unread(self):
        return self.last_read is None

    def is_eof(self):
        return self.last_read == ""

    def next_eof(self):
        return self.next_read == ""

    def close(self):
        self.file_obj.close()


class FileRecorder:
    def __init__(self, filename: str):
        self.filename = filename
        self.file_obj = None
        # debugging purposes
        self.n_writes = 0

    def open(self):
        self.file_obj = open(self.filename, "w")

    def write(self, txt: str):
        self.file_obj.write(txt)
        self.n_writes += 1

    def close(self):
        self.file_obj.close()


class Statistics:
    def __init__(self, filename: str,
                 total_bytes: int=0,
                 data_packet_size: int=0,
                 buffer_size: int=0,
                 window_size: int=0,
                 timeout: float=0.0,
                 n_repetitions: int=0):
        self.filename = filename
        # data on table
        self.table = OrderedDict(FILE_BYTES=total_bytes,
                                 PKG_SIZE=data_packet_size,
                                 BUF_SIZE=buffer_size,
                                 WIN_SIZE=window_size,
                                 TIMEOUT=timeout,
                                 AVG_TIME=0,
                                 STD_TIME=0)
        # data list to process later
        self.times = []
        # extra data
        self.n_of_packets = ceil(total_bytes / data_packet_size)
        self.n_repetitions = n_repetitions
        self.create_when_empty()

    def create_when_empty(self):
        if not os.path.isfile(self.filename):
            with open(self.filename, "w") as f:
                pass

    def clear(self):
        self.times = []

    def add_time(self, a_time):
        self.times.append(a_time)
        self.table["AVG_TIME"] = 0.0
        self.table["STD_TIME"] = 0.0

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

    def update_time_statistics(self):
        assert self.n_repetitions == len(self.times)

        self.table["AVG_TIME"] = round(self.mean(self.times),5)
        self.table["STD_TIME"] = round(self.std(self.times),5)

    def template(self):
        """
        Template for the statistics message for the repeated tests
        """
        return "\n=====ESTADÍSTICAS FINALES=====\n" \
               "Total de bytes enviados: \t{FILE_BYTES}\n" \
               "Tamaño de un paquete (solo data): \t\t{PKG_SIZE}\n" \
               "Total paquetes enviados: \t{n_of_packets}\n\n" \
               "Número de repeticiones:\t{n_repetitions}\n" \
               "Tamaño del buffer:\t{BUF_SIZE}\n" \
               "Tamaño ventana:\t{WIN_SIZE}\n" \
               "Timeout por pérdida:\t{TIMEOUT}\n" \
               "tiempo tomado promedio:\t\t{AVG_TIME}\n" \
               "desv. estándar de tiempo:\t{STD_TIME}\n".format(n_of_packets=self.n_of_packets,
                                                                n_repetitions=self.n_repetitions,
                                                                **self.table)

    def print_statistics(self):
        self.update_time_statistics()
        print(self.template())

    def non_empty_file(self):
        return os.path.isfile(self.filename) and os.path.getsize(self.filename) > 0

    def save(self):
        not_empty = self.non_empty_file()
        with open(self.filename, "a") as fout:
            dw = csv.DictWriter(fout, delimiter=",",
                                fieldnames=list(self.table.keys()))
            if not not_empty:
                dw.writeheader()
            dw.writerow(self.table)


class FIFOSemaphore(Queue):
    def __init__(self, value=1):
        super().__init__()
        self.release(value)

    def acquire(self, blocking=True, timeout=None):
        try:
            return self.get(blocking, timeout)
        except Empty:
            return False


    def release(self, n=1, additional="tix"):
        for v in range(n):
            self.put(additional)

    def value(self):
        return self.qsize()


