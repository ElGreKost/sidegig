// HTTP client for the sidegig server.
//
// Every request carries a fresh Firebase ID token. Any reply with a 4xx/5xx
// status or with `"ok": false` in its body becomes an [ApiException].

import 'dart:async';
import 'dart:convert';

import 'package:firebase_auth/firebase_auth.dart';
import 'package:http/http.dart' as http;

import 'config.dart';
import 'models.dart';

class ApiException implements Exception {
  ApiException(this.error, {this.hint, this.code, this.status});

  final String error;
  final String? hint;
  final String? code;
  final int? status;

  /// The server refused this account (not signed in, or no access).
  bool get isAuth => status == 401 || status == 403;

  /// A short line for a SnackBar: the error, then the hint when there is one.
  String get message => hint == null || hint!.isEmpty ? error : '$error\n$hint';

  @override
  String toString() => message;
}

class ApiClient {
  /// [idToken] replaces the Firebase token lookup (for previews and tests).
  ApiClient({Future<String?> Function()? idToken}) : _idToken = idToken;

  final Future<String?> Function()? _idToken;

  final http.Client _http = http.Client();
  bool _closed = false;

  static const _timeout = Duration(seconds: 30);

  /// Long polls wait up to 25 s on the server; leave room for the network.
  static const _pollTimeout = Duration(seconds: 45);

  void close() {
    _closed = true;
    _http.close();
  }

  Future<Me> me() async => Me.fromJson(await _get('/v1/me'));

  Future<EventBatch> events({required int after, required String boot}) async {
    final j = await _get(
      '/v1/events',
      query: {'after': '$after', 'wait': '25', if (boot.isNotEmpty) 'boot': boot},
      timeout: _pollTimeout,
    );
    return EventBatch.fromJson(j);
  }

  Future<List<Gig>> gigList({String? status, int limit = 200}) async {
    final j = await callTool('gig_list', {'limit': limit, if (status != null) 'status': status});
    return Gig.listFrom(j['gigs']);
  }

  Future<GigThread> gigRead(String gigId) async =>
      GigThread.fromJson(await callTool('gig_read', {'gig_id': gigId, 'as_messages': true}));

  Future<AgentDirectory> agentList() async => AgentDirectory.fromJson(await callTool('agent_list', {}));

  Future<InboxData> inbox() async => InboxData.fromJson(await callTool('inbox', {}));

  /// Starts a new gig and returns its id.
  Future<String> gigPost({
    required String title,
    required String text,
    required String toAgent,
    String? actAs,
  }) async {
    final j = await callTool('gig_post', {'title': title, 'text': text, 'to_agent': toAgent}, actAs: actAs);
    final id = (j['gig_id'] ?? j['id'])?.toString() ?? '';
    if (id.isEmpty) throw ApiException('The server did not return the new conversation id.');
    return id;
  }

  Future<void> gigFollowup(String gigId, String text, {String? actAs}) =>
      callTool('gig_followup', {'gig_id': gigId, 'text': text}, actAs: actAs);

  Future<void> gigSay(String gigId, String text, {String? actAs}) =>
      callTool('gig_say', {'gig_id': gigId, 'text': text}, actAs: actAs);

  Future<void> gigClose(String gigId, {String? note, String? actAs}) => callTool(
        'gig_close',
        {'gig_id': gigId, if (note != null && note.isNotEmpty) 'note': note},
        actAs: actAs,
      );

  /// Calls one board tool. [actAs] sets `X-Sidegig-As`; pass it only for
  /// writes made as one of the person's other agents.
  Future<Map<String, dynamic>> callTool(String tool, Map<String, dynamic> args, {String? actAs}) async {
    final headers = await _headers();
    headers['Content-Type'] = 'application/json';
    if (actAs != null && actAs.isNotEmpty) headers['X-Sidegig-As'] = actAs;
    return _send(() => _http.post(_uri('/v1/call/$tool'), headers: headers, body: jsonEncode(args)), _timeout);
  }

  Future<Map<String, dynamic>> _get(String path, {Map<String, String>? query, Duration timeout = _timeout}) async {
    final headers = await _headers();
    return _send(() => _http.get(_uri(path, query), headers: headers), timeout);
  }

  Uri _uri(String path, [Map<String, String>? query]) {
    final base = AppConfig.serverBase;
    if (base == null) {
      throw ApiException('This build has no server address.', hint: 'Build it with --dart-define=SIDEGIG_API=<server url>.');
    }
    final uri = Uri.parse('$base$path');
    return query == null ? uri : uri.replace(queryParameters: query);
  }

  Future<Map<String, String>> _headers() async {
    String? token;
    if (_idToken != null) {
      token = await _idToken();
    } else {
      final user = FirebaseAuth.instance.currentUser;
      if (user == null) throw ApiException('You are signed out.', status: 401);
      try {
        token = await user.getIdToken();
      } catch (_) {
        token = null;
      }
    }
    if (token == null || token.isEmpty) {
      throw ApiException('Could not refresh your sign-in.', hint: 'Sign out and sign in again.', status: 401);
    }
    return {'Authorization': 'Bearer $token', 'Accept': 'application/json'};
  }

  Future<Map<String, dynamic>> _send(Future<http.Response> Function() request, Duration timeout) async {
    if (_closed) throw ApiException('Signed out.');
    final http.Response res;
    try {
      res = await request().timeout(timeout);
    } on TimeoutException {
      throw ApiException('The server took too long to answer.', code: 'timeout');
    } on ApiException {
      rethrow;
    } catch (_) {
      throw ApiException('Could not reach the server.', hint: 'Check your connection and try again.', code: 'network');
    }

    Object? body;
    try {
      body = jsonDecode(utf8.decode(res.bodyBytes));
    } catch (_) {
      body = null;
    }
    final j = body is Map ? body.map((k, v) => MapEntry(k.toString(), v)) : <String, dynamic>{};

    if (res.statusCode >= 400 || j['ok'] == false || body is! Map) {
      final error = j['error']?.toString();
      throw ApiException(
        error == null || error.isEmpty ? 'The server answered with HTTP ${res.statusCode}.' : error,
        hint: j['hint']?.toString(),
        code: j['code']?.toString(),
        status: res.statusCode,
      );
    }
    return j;
  }
}
