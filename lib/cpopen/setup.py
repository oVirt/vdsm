from distutils.core import setup, Extension

module1 = Extension('cpopen',
                    sources=['cpopen.c'])

setup(name='cpopen',
      version='1.2.1',
      description='Creates a subprocess in simpler safer manner',
      py_modules=['cpopen'],
      author='Yaniv Bronhaim',
      author_email='ybronhei@redhat.com',
      url='redhat.com',
      ext_modules=[module1])
