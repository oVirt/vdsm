from distutils.core import setup, Extension

module1 = Extension('createprocess',
                    sources=['createprocess.c'])

setup(name='createprocess',
      version='1.0',
      description='Creates a subprocess in simpler safer manner',
      ext_modules=[module1])
