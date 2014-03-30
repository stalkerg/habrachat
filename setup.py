#!/usr/bin/env python

try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

install_requires=[
	'setuptools',
	'Tornado >= 3.2', 
	'python-dateutil >= 1.5',
	'tornado-redis >= 2.4.16',
	'pytz'
]

setup(name='habrachat',
	version='1.0',
	description='habrachat',
	author='stalkerg',
	author_email='stalkerg@gmail.com',
	url='http://habrachat.net',
	#packages=['distutils', 'distutils.command'],
	install_requires = install_requires,
)