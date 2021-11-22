from utils import MyParser
import os.path


def generate_packet(byte_size):
    """Para generar un archivo de un tamaño arbitrario en bytes, puede usar la
    siguiente función, que guardará un archivo de texto con el prefijo paquetes_:"""
    s = '0'
    i = 0

    while len(s) < byte_size:
        i = (i + 1) % 10
        s += str(i)
    with open(os.path.join("transfer_files", f"paquetes_{byte_size}.txt"), 'w') as f:
        f.write(s)


if __name__ == '__main__':
    parser = MyParser()
    parser.add_argument("n", help="Size in bytes of the generated file.",
                        type=int)

    args = parser.parse_args()

    generate_packet(args.n)
