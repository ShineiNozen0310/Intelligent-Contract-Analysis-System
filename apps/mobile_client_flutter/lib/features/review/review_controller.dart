import 'dart:async';
import 'dart:convert';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:flutter/foundation.dart';
import 'package:path_provider/path_provider.dart';

import '../../core/api/local_api_client.dart';
import '../../core/models/review_models.dart';

class ReviewController extends ChangeNotifier {
  ReviewController({String? baseUrl})
      : _baseUrl = baseUrl ?? _defaultBaseUrl(),
        _client = LocalApiClient(baseUrl ?? _defaultBaseUrl());

  final LocalApiClient _client;

  String _baseUrl;
  bool _loading = false;
  bool _polling = false;
  int? _jobId;
  String _status = 'idle';
  int _progress = 0;
  String _stage = '';
  String _filePath = '';
  String _fileName = '';
  String _message = '';
  String _error = '';
  String _reportMarkdown = '';
  Map<String, dynamic>? _reportPayload;
  Map<String, dynamic>? _resultJson;
  String _exportedPath = '';
  DateTime? _reviewStartedAt;
  DateTime? _reviewFinishedAt;

  Timer? _pollTimer;

  String get baseUrl => _baseUrl;
  bool get loading => _loading;
  bool get polling => _polling;
  int? get jobId => _jobId;
  String get status => _status;
  int get progress => _progress;
  String get stage => _stage;
  String get filePath => _filePath;
  String get fileName => _fileName;
  String get message => _message;
  String get error => _error;
  String get reportMarkdown => _reportMarkdown;
  Map<String, dynamic>? get reportPayload => _reportPayload;
  Map<String, dynamic>? get resultJson => _resultJson;
  String get exportedPath => _exportedPath;

  Duration? get reviewElapsed {
    final start = _reviewStartedAt;
    if (start == null) {
      return null;
    }
    final end = _reviewFinishedAt ?? DateTime.now();
    if (end.isBefore(start)) {
      return Duration.zero;
    }
    return end.difference(start);
  }

  String get reviewElapsedText {
    final elapsed = reviewElapsed;
    if (elapsed == null) {
      return '--';
    }
    return _formatDuration(elapsed);
  }

  String get prettyJson {
    final data = _resultJson;
    if (data == null) {
      return '';
    }
    return const JsonEncoder.withIndent('  ').convert(data);
  }

  Future<void> updateBaseUrl(String value) async {
    _baseUrl = value.trim();
    _client.baseUrl = _baseUrl;
    notifyListeners();
  }

  Future<void> checkHealth() async {
    await _runGuarded(() async {
      Map<String, dynamic> data = const {'ok': false};
      Object? lastError;
      for (var i = 0; i < 4; i++) {
        try {
          data = await _client.health();
          if (data['ok'] == true) {
            _message = '本地 API 可用';
            _error = '';
            return;
          }
        } catch (e) {
          lastError = e;
        }
        if (i != 3) {
          await Future<void>.delayed(const Duration(milliseconds: 800));
        }
      }

      if (lastError != null) {
        _error = '本地 API 未就绪，请等待 3-5 秒后重试。';
      } else {
        _error = (data['error'] ?? '本地 API 健康检查失败').toString();
      }
    });
  }


  Future<void> pickPdf() async {
    _clearNotice();
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: const ['pdf'],
      withData: true,
    );
    if (result == null || result.files.isEmpty) {
      return;
    }
    final picked = result.files.first;
    File? file;
    if (picked.path != null && picked.path!.isNotEmpty) {
      file = File(picked.path!);
    } else if (picked.bytes != null) {
      file = await _createTempPdf(picked);
    }
    if (file == null) {
      _error = '无法读取所选 PDF 文件';
      notifyListeners();
      return;
    }
    _filePath = file.path;
    _fileName = picked.name;
    notifyListeners();
  }

  Future<void> startReview() async {
    if (_filePath.isEmpty) {
      _error = '请先选择 PDF 文件';
      notifyListeners();
      return;
    }

    await _runGuarded(() async {
      _stopPolling();
      _status = 'submitting';
      _progress = 0;
      _stage = 'start';
      _resultJson = null;
      _reportPayload = null;
      _reportMarkdown = '';
      _exportedPath = '';
      _reviewStartedAt = DateTime.now();
      _reviewFinishedAt = null;
      notifyListeners();

      final id = await _client.startAnalyze(File(_filePath));
      _jobId = id;
      _status = 'running';
      _stage = 'submitted';
      _progress = 1;
      _message = '任务已提交，开始轮询进度';
      _error = '';
      notifyListeners();
      _startPolling();
    });
  }

  Future<void> refreshResult() async {
    final id = _jobId;
    if (id == null) {
      return;
    }
    await _runGuarded(() async {
      final result = await _client.fetchResult(id);
      _status = result.status;
      _stage = result.stage;
      _progress = result.progress;
      _resultJson = result.resultJson;
      _reportPayload = result.reportPayload;
      _reportMarkdown = _selectReportText(result: result);
      if (result.status == 'done' || result.status == 'error') {
        _reviewFinishedAt ??= DateTime.now();
      }
      if (result.error.isNotEmpty) {
        _error = result.error;
      }
    });
  }

  Future<void> exportPdf() async {
    final id = _jobId;
    if (id == null) {
      _error = '请先执行审查任务';
      notifyListeners();
      return;
    }
    await _runGuarded(() async {
      final output = await _client.exportPdf(
        id,
        preferredName: _buildOutputName(),
      );
      _exportedPath = output;
      _message = 'PDF 已导出：$output';
    });
  }

  Future<void> clearAll() async {
    _stopPolling();
    _jobId = null;
    _status = 'idle';
    _progress = 0;
    _stage = '';
    _resultJson = null;
    _reportPayload = null;
    _reportMarkdown = '';
    _exportedPath = '';
    _message = '';
    _error = '';
    _reviewStartedAt = null;
    _reviewFinishedAt = null;
    notifyListeners();
  }

  @override
  void dispose() {
    _stopPolling();
    super.dispose();
  }

  Future<void> _runGuarded(Future<void> Function() task) async {
    _loading = true;
    notifyListeners();
    try {
      await task();
    } on ApiException catch (e) {
      _error = e.message;
    } catch (e) {
      _error = e.toString();
    } finally {
      _loading = false;
      notifyListeners();
    }
  }

  void _startPolling() {
    _stopPolling();
    _polling = true;
    notifyListeners();
    _pollTimer = Timer.periodic(const Duration(seconds: 1), (_) async {
      await _pollOnce();
    });
  }

  void _stopPolling() {
    _pollTimer?.cancel();
    _pollTimer = null;
    _polling = false;
  }

  Future<void> _pollOnce() async {
    final id = _jobId;
    if (id == null) {
      _stopPolling();
      return;
    }
    try {
      final data = await _client.fetchStatus(id);
      _status = data.status;
      _progress = data.progress;
      _stage = data.stage;
      if (data.resultJson != null) {
        _resultJson = data.resultJson;
      }
      if (data.reportPayload != null) {
        _reportPayload = data.reportPayload;
      }
      if (data.resultMarkdown.isNotEmpty) {
        _reportMarkdown = data.resultMarkdown;
      }
      if (data.isDone || data.isError) {
        _stopPolling();
        _reviewFinishedAt ??= DateTime.now();
        final result = await _client.fetchResult(id);
        _resultJson = result.resultJson;
        _reportPayload = result.reportPayload;
        _reportMarkdown = _selectReportText(result: result);
        if (result.error.isNotEmpty) {
          _error = result.error;
        } else {
          _message = '任务已完成';
        }
      }
      notifyListeners();
    } catch (e) {
      _stopPolling();
      _reviewFinishedAt ??= DateTime.now();
      _error = e.toString();
      notifyListeners();
    }
  }

  Future<File> _createTempPdf(PlatformFile file) async {
    final dir = await getTemporaryDirectory();
    final path = '${dir.path}${Platform.pathSeparator}${file.name}';
    final out = File(path);
    await out.writeAsBytes(file.bytes!, flush: true);
    return out;
  }

  String _selectReportText({required JobResult result}) {
    if (result.reportMarkdown.trim().isNotEmpty) {
      return result.reportMarkdown;
    }
    if (result.resultMarkdown.trim().isNotEmpty) {
      return result.resultMarkdown;
    }
    return '';
  }

  static String _formatDuration(Duration d) {
    final hours = d.inHours;
    final minutes = d.inMinutes.remainder(60);
    final seconds = d.inSeconds.remainder(60);
    if (hours > 0) {
      return '${hours.toString().padLeft(2, '0')}:${minutes.toString().padLeft(2, '0')}:${seconds.toString().padLeft(2, '0')}';
    }
    return '${minutes.toString().padLeft(2, '0')}:${seconds.toString().padLeft(2, '0')}';
  }

  String _buildOutputName() {
    if (_fileName.isNotEmpty) {
      final dot = _fileName.lastIndexOf('.');
      final stem = dot > 0 ? _fileName.substring(0, dot) : _fileName;
      return '${stem}_审查报告.pdf';
    }
    final id = _jobId ?? 0;
    return 'contract_review_job_$id.pdf';
  }

  void _clearNotice() {
    _message = '';
    _error = '';
  }

  static String _defaultBaseUrl() {
    if (!kIsWeb && Platform.isAndroid) {
      return 'http://10.0.2.2:8003/contract';
    }
    return 'http://127.0.0.1:8003/contract';
  }
}
