import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:http/http.dart' as http;
import 'package:path_provider/path_provider.dart';

import '../models/review_models.dart';

class LocalApiClient {
  LocalApiClient(String baseUrl) : _baseUrl = _normalizeBase(baseUrl);

  String _baseUrl;

  String get baseUrl => _baseUrl;

  set baseUrl(String value) {
    _baseUrl = _normalizeBase(value);
  }

  Future<Map<String, dynamic>> health() async {
    final resp = await http
        .get(_uri('/api/health/'))
        .timeout(const Duration(seconds: 10));
    return _readJson(resp);
  }

  Future<int> startAnalyze(File pdfFile) async {
    final req = http.MultipartRequest('POST', _uri('/api/start/'));
    req.files.add(await http.MultipartFile.fromPath('file', pdfFile.path));
    final streamed = await req.send().timeout(const Duration(seconds: 120));
    final resp = await http.Response.fromStream(streamed);
    final data = _readJson(resp);
    if (resp.statusCode >= 400 || data['ok'] != true) {
      throw ApiException(_errorText(data, fallback: '任务启动失败'));
    }
    final id = data['job_id'];
    if (id is int) {
      return id;
    }
    if (id is String) {
      final parsed = int.tryParse(id);
      if (parsed != null) {
        return parsed;
      }
    }
    throw ApiException('API 返回了无效的 job_id');
  }

  Future<JobStatus> fetchStatus(int jobId) async {
    final resp = await http
        .get(_uri('/api/status/$jobId/'))
        .timeout(const Duration(seconds: 20));
    final data = _readJson(resp);
    if (resp.statusCode >= 400) {
      throw ApiException(_errorText(data, fallback: '任务状态查询失败'));
    }
    return JobStatus.fromJson(data);
  }

  Future<JobResult> fetchResult(int jobId) async {
    final resp = await http
        .get(_uri('/api/result/$jobId/'))
        .timeout(const Duration(seconds: 20));
    final data = _readJson(resp);
    if (resp.statusCode >= 400) {
      throw ApiException(_errorText(data, fallback: '审查结果查询失败'));
    }
    return JobResult.fromJson(data);
  }

  Future<String> exportPdf(int jobId, {String? preferredName}) async {
    final resp = await http
        .get(_uri('/api/export_pdf/$jobId/'))
        .timeout(const Duration(seconds: 120));
    if (resp.statusCode != 200) {
      final data = _readJson(resp);
      throw ApiException(_errorText(data, fallback: 'PDF 导出失败'));
    }
    final bytes = resp.bodyBytes;
    return _savePdfBytes(
      bytes,
      preferredName: preferredName ?? 'contract_review_job_$jobId.pdf',
    );
  }

  Uri _uri(String path) => Uri.parse('$_baseUrl$path');

  static String _normalizeBase(String input) {
    var s = input.trim();
    if (s.endsWith('/')) {
      s = s.substring(0, s.length - 1);
    }
    if (s.endsWith('/contract')) {
      return s;
    }
    return '$s/contract';
  }

  Map<String, dynamic> _readJson(http.Response resp) {
    if (resp.body.isEmpty) {
      return {'ok': false, 'error': '空响应'};
    }
    try {
      final decoded = jsonDecode(resp.body);
      if (decoded is Map<String, dynamic>) {
        return decoded;
      }
      return {'ok': false, 'error': '响应格式无效'};
    } catch (_) {
      return {'ok': false, 'error': resp.body};
    }
  }

  String _errorText(Map<String, dynamic> data, {required String fallback}) {
    final detail = data['detail'];
    if (detail is String && detail.trim().isNotEmpty) {
      return detail.trim();
    }
    final error = data['error'];
    if (error is String && error.trim().isNotEmpty) {
      return error.trim();
    }
    return fallback;
  }

  Future<String> _savePdfBytes(Uint8List bytes,
      {required String preferredName}) async {
    final targetDir = await _resolveOutputDirectory();
    final target = File('${targetDir.path}${Platform.pathSeparator}$preferredName');
    await target.writeAsBytes(bytes, flush: true);
    return target.path;
  }

  Future<Directory> _resolveOutputDirectory() async {
    final downloadDir = await getDownloadsDirectory();
    if (downloadDir != null) {
      return downloadDir;
    }
    return getApplicationDocumentsDirectory();
  }
}

class ApiException implements Exception {
  ApiException(this.message);

  final String message;

  @override
  String toString() => message;
}
