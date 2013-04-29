import sys
from setuptools import setup, find_packages

install_requires = [
    'avocado>=2.0.22,<2.1',
    'restlib2>=0.3.2,<0.4',
    'django-preserialize>=1.0.4,<1.1',
]

if sys.version_info < (2, 7):
    install_requires.append('ordereddict>=1.1')

kwargs = {
    # Packages
    'packages': find_packages(exclude=['tests', '*.tests', '*.tests.*', 'tests.*']),
    'include_package_data': True,

    # Dependencies
    'install_requires': install_requires,

    # Test dependencies
    'tests_require': [
        'avocado[permissions,search,clustering,extras]>=2.0.22,<2.1'
        'coverage',
        'whoosh',
    ],

    'test_suite': 'test_suite',

    # Optional dependencies
    'extras_require': {},

    # Metadata
    'name': 'serrano',
    'version': __import__('serrano').get_version(),
    'author': 'Byron Ruth',
    'author_email': 'b@devel.io',
    'description': 'Hypermedia implementation for Avocado',
    'license': 'BSD',
    'keywords': 'hypermedia rest api avocado serrano cilantro harvest',
    'url': 'http://cbmi.github.com/serrano/',
    'classifiers': [
        'Development Status :: 5 - Production/Stable',
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python :: 2.6',
        'Programming Language :: Python :: 2.7',
        'Framework :: Django',
        'Topic :: Internet :: WWW/HTTP',
        'Intended Audience :: Developers',
        'Intended Audience :: Science/Research',
        'Intended Audience :: Healthcare Industry',
        'Intended Audience :: Information Technology',
    ],
}

setup(**kwargs)
