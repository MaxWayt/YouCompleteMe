#!/usr/bin/env python
#
# Copyright (C) 2011, 2012  Chiel ten Brinke <ctenbrinke@gmail.com>
#                           Strahinja Val Markovic <val@markovic.io>
#
# This file is part of YouCompleteMe.
#
# YouCompleteMe is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# YouCompleteMe is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with YouCompleteMe.  If not, see <http://www.gnu.org/licenses/>.

import os
from sys import platform
import glob
from ycm.completers.completer import Completer
from ycm.server import responses
from ycm import utils
import urllib2
import urllib
import urlparse
import json
import subprocess
import logging

SERVER_NOT_FOUND_MSG = ( 'OmniSharp server binary not found at {0}. ' +
'Did you compile it? You can do so by running ' +
'"./install.sh --omnisharp-completer".' )


class CsharpCompleter( Completer ):
  """
  A Completer that uses the Omnisharp server as completion engine.
  """

  def __init__( self, user_options ):
    super( CsharpCompleter, self ).__init__( user_options )
    self._omnisharp_port = None
    self._logger = logging.getLogger( __name__ )


  def Shutdown( self ):
    if ( self.user_options[ 'auto_start_csharp_server' ] and
         self._ServerIsRunning() ):
      self._StopServer()


  def SupportedFiletypes( self ):
    """ Just csharp """
    return [ 'cs' ]


  def ComputeCandidatesInner( self, request_data ):
    return [ responses.BuildCompletionData(
                completion[ 'CompletionText' ],
                completion[ 'DisplayText' ],
                completion[ 'Description' ] )
             for completion in self._GetCompletions( request_data ) ]


  def DefinedSubcommands( self ):
    return [ 'StartServer',
             'StopServer',
             'RestartServer',
             'ServerRunning',
             'GoToDefinition',
             'GoToDeclaration',
             'GoToDefinitionElseDeclaration' ]


  def OnFileReadyToParse( self, request_data ):
    if ( not self._omnisharp_port and
         self.user_options[ 'auto_start_csharp_server' ] ):
      self._StartServer( request_data )


  def OnUserCommand( self, arguments, request_data ):
    if not arguments:
      raise ValueError( self.UserCommandsHelpMessage() )

    command = arguments[ 0 ]
    if command == 'StartServer':
      self._StartServer( request_data )
    elif command == 'StopServer':
      self._StopServer()
    elif command == 'RestartServer':
      if self._ServerIsRunning():
        self._StopServer()
      self._StartServer( request_data )
    elif command == 'ServerRunning':
      return self._ServerIsRunning()
    elif command in [ 'GoToDefinition',
                      'GoToDeclaration',
                      'GoToDefinitionElseDeclaration' ]:
      return self._GoToDefinition( request_data )
    raise ValueError( self.UserCommandsHelpMessage() )


  def DebugInfo( self ):
    if self._ServerIsRunning():
      return 'Server running at: {0}\nLogfiles:\n{1}\n{2}'.format(
        self._ServerLocation(), self._filename_stdout, self._filename_stderr )
    else:
      return 'Server is not running'


  def _StartServer( self, request_data ):
    """ Start the OmniSharp server """
    self._logger.info( 'startup' )

    self._omnisharp_port = utils.GetUnusedLocalhostPort()
    solutionfiles, folder = _FindSolutionFiles( request_data[ 'filepath' ] )

    if len( solutionfiles ) == 0:
      raise RuntimeError(
        'Error starting OmniSharp server: no solutionfile found' )
    elif len( solutionfiles ) == 1:
      solutionfile = solutionfiles[ 0 ]
    else:
      raise RuntimeError(
        'Found multiple solution files instead of one!\n{0}'.format(
          solutionfiles ) )

    omnisharp = os.path.join(
      os.path.abspath( os.path.dirname( __file__ ) ),
      'OmniSharpServer/OmniSharp/bin/Debug/OmniSharp.exe' )

    if not os.path.isfile( omnisharp ):
      raise RuntimeError( SERVER_NOT_FOUND_MSG.format( omnisharp ) )

    if not platform.startswith( 'win' ):
      omnisharp = 'mono ' + omnisharp

    path_to_solutionfile = os.path.join( folder, solutionfile )
    # command has to be provided as one string for some reason
    command = [ omnisharp + ' -p ' + str( self._omnisharp_port ) + ' -s ' +
                path_to_solutionfile ]

    filename_format = os.path.join( utils.PathToTempDir(),
                                   'omnisharp_{port}_{sln}_{std}.log' )

    self._filename_stdout = filename_format.format(
        port=self._omnisharp_port, sln=solutionfile, std='stdout' )
    self._filename_stderr = filename_format.format(
        port=self._omnisharp_port, sln=solutionfile, std='stderr' )

    with open( self._filename_stderr, 'w' ) as fstderr:
      with open( self._filename_stdout, 'w' ) as fstdout:
        subprocess.Popen( command, stdout=fstdout, stderr=fstderr, shell=True )

    self._logger.info( 'Starting OmniSharp server' )


  def _StopServer( self ):
    """ Stop the OmniSharp server """
    self._GetResponse( '/stopserver' )
    self._omnisharp_port = None
    self._logger.info( 'Stopping OmniSharp server' )


  def _GetCompletions( self, request_data ):
    """ Ask server for completions """
    completions = self._GetResponse( '/autocomplete',
                                     self._DefaultParameters( request_data ) )
    return completions if completions != None else []


  def _GoToDefinition( self, request_data ):
    """ Jump to definition of identifier under cursor """
    definition = self._GetResponse( '/gotodefinition',
                                    self._DefaultParameters( request_data ) )
    if definition[ 'FileName' ] != None:
      return responses.BuildGoToResponse( definition[ 'FileName' ],
                                          definition[ 'Line' ],
                                          definition[ 'Column' ] )
    else:
      raise RuntimeError( 'Can\'t jump to definition' )


  def _DefaultParameters( self, request_data ):
    """ Some very common request parameters """
    parameters = {}
    parameters[ 'line' ] = request_data[ 'line_num' ] + 1
    parameters[ 'column' ] = request_data[ 'column_num' ] + 1
    filepath = request_data[ 'filepath' ]
    parameters[ 'buffer' ] = request_data[ 'file_data' ][ filepath ][
      'contents' ]
    parameters[ 'filename' ] = filepath
    return parameters


  def _ServerIsRunning( self ):
    """ Check if our OmniSharp server is running """
    try:
      return bool( self._omnisharp_port and
                  self._GetResponse( '/checkalivestatus', silent = True ) )
    except:
      return False


  def _ServerLocation( self ):
    return 'http://localhost:' + str( self._omnisharp_port )


  def _GetResponse( self, handler, parameters = {}, silent = False ):
    """ Handle communication with server """
    # TODO: Replace usage of urllib with Requests
    target = urlparse.urljoin( self._ServerLocation(), handler )
    parameters = urllib.urlencode( parameters )
    response = urllib2.urlopen( target, parameters )
    return json.loads( response.read() )


def _FindSolutionFiles( filepath ):
  """ Find solution files by searching upwards in the file tree """
  folder = os.path.dirname( filepath )
  solutionfiles = glob.glob1( folder, '*.sln' )
  while not solutionfiles:
    lastfolder = folder
    folder = os.path.dirname( folder )
    if folder == lastfolder:
      break
    solutionfiles = glob.glob1( folder, '*.sln' )
  return solutionfiles, folder
