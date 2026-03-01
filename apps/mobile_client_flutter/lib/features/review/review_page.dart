import 'package:flutter/material.dart';

import 'review_controller.dart';

class ReviewPage extends StatefulWidget {
  const ReviewPage({super.key, this.startupNotice = ''});

  final String startupNotice;

  @override
  State<ReviewPage> createState() => _ReviewPageState();
}

enum _ItemTone { risk, improve }

class _ReviewPageState extends State<ReviewPage> {
  final ReviewController _controller = ReviewController();

  @override
  void initState() {
    super.initState();
    _controller.addListener(_onStateChanged);
    if (widget.startupNotice.trim().isNotEmpty) {
      _controller.applyStartupNotice(widget.startupNotice);
    }
    _controller.checkUpdateSilently();
  }

  @override
  void dispose() {
    _controller.removeListener(_onStateChanged);
    _controller.dispose();
    super.dispose();
  }

  void _onStateChanged() {
    if (!mounted) {
      return;
    }
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final width = MediaQuery.of(context).size.width;
    final isWide = width >= 980;

    return Scaffold(
      body: Container(
        decoration: const BoxDecoration(
          gradient: LinearGradient(
            colors: [Color(0xFFE7F2F0), Color(0xFFF6F8FB), Color(0xFFFFF5EA)],
            begin: Alignment.topLeft,
            end: Alignment.bottomRight,
          ),
        ),
        child: SafeArea(
          child: Stack(
            children: [
              SingleChildScrollView(
                padding: const EdgeInsets.all(16),
                child: Center(
                  child: ConstrainedBox(
                    constraints: const BoxConstraints(maxWidth: 1280),
                    child: Column(
                      children: [
                        _buildProductHero(),
                        const SizedBox(height: 14),
                        isWide ? _buildWideLayout() : _buildNarrowLayout(),
                      ],
                    ),
                  ),
                ),
              ),
              if (_controller.loading)
                const Positioned.fill(
                  child: ColoredBox(
                    color: Color(0x88000000),
                    child: Center(child: CircularProgressIndicator()),
                  ),
                ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildWideLayout() {
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          flex: 4,
          child: Column(
            children: [
              _buildWorkflowCard(),
              const SizedBox(height: 14),
              _buildHeaderCard(),
              const SizedBox(height: 14),
              _buildControlCard(),
              const SizedBox(height: 14),
              _buildProgressCard(),
              const SizedBox(height: 14),
              _buildRuntimeMetricsCard(),
              const SizedBox(height: 14),
              _buildUpdateCard(),
            ],
          ),
        ),
        const SizedBox(width: 14),
        Expanded(
          flex: 7,
          child: Column(
            children: [
              _buildResultCard(),
            ],
          ),
        ),
      ],
    );
  }

  Widget _buildNarrowLayout() {
    return Column(
      children: [
        _buildWorkflowCard(),
        const SizedBox(height: 14),
        _buildHeaderCard(),
        const SizedBox(height: 14),
        _buildControlCard(),
        const SizedBox(height: 14),
        _buildProgressCard(),
        const SizedBox(height: 14),
        _buildRuntimeMetricsCard(),
        const SizedBox(height: 14),
        _buildUpdateCard(),
        const SizedBox(height: 14),
        _buildResultCard(),
      ],
    );
  }

  Widget _buildProductHero() {
    final progress = _controller.progress.clamp(0, 100);

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        gradient: const LinearGradient(
          colors: [Color(0xFF0A7B72), Color(0xFF215CA7)],
          begin: Alignment.topLeft,
          end: Alignment.bottomRight,
        ),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0x336A92C6)),
        boxShadow: const [
          BoxShadow(
            color: Color(0x220A2548),
            blurRadius: 18,
            offset: Offset(0, 8),
          ),
        ],
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 42,
                height: 42,
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.18),
                  borderRadius: BorderRadius.circular(12),
                ),
                child: const Icon(Icons.fact_check_rounded, color: Colors.white),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '\u5408\u540c\u667a\u80fd\u5ba1\u67e5',
                      style: Theme.of(context).textTheme.titleLarge?.copyWith(
                            fontWeight: FontWeight.w900,
                            color: Colors.white,
                          ),
                    ),
                    const SizedBox(height: 2),
                    Text(
                      '\u9762\u5411\u4e1a\u52a1\u7528\u6237\u7684\u5ba1\u67e5\u7ed3\u679c\u5c55\u793a',
                      style: Theme.of(context).textTheme.bodySmall?.copyWith(
                            color: const Color(0xFFD3E6FF),
                          ),
                    ),
                  ],
                ),
              ),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 7),
                decoration: BoxDecoration(
                  color: Colors.white.withValues(alpha: 0.18),
                  borderRadius: BorderRadius.circular(999),
                  border: Border.all(color: Colors.white.withValues(alpha: 0.26)),
                ),
                child: Text(
                  _statusToCn(_controller.status),
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.w800,
                    fontSize: 12,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _heroMetric(
                '\u5f53\u524d\u5408\u540c',
                _controller.fileName.isEmpty ? '\u672a\u9009\u62e9\u5408\u540c' : _controller.fileName,
              ),
              _heroMetric(
                '\u5f53\u524d\u9636\u6bb5',
                _controller.stage.isEmpty ? '-' : _stageToCn(_controller.stage),
              ),
              _heroMetric('\u5ba1\u67e5\u7528\u65f6', _controller.reviewElapsedText),
              _heroMetric('\u8fdb\u5ea6', '$progress%'),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildWorkflowCard() {
    final current = _workflowIndex();
    const steps = [
      '1. 连接服务',
      '2. 上传合同',
      '3. 执行审查',
      '4. 查看与导出',
    ];

    return _panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '流程导航',
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          Text(
            '当前阶段会高亮，便于定位问题与重试环节。',
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(color: const Color(0xFF607388)),
          ),
          const SizedBox(height: 12),
          for (var i = 0; i < steps.length; i++) ...[
            _workflowStep(
              text: steps[i],
              done: i < current,
              active: i == current,
            ),
            if (i != steps.length - 1) const SizedBox(height: 8),
          ],
        ],
      ),
    );
  }

  Widget _workflowStep({
    required String text,
    required bool done,
    required bool active,
  }) {
    final color = done
        ? const Color(0xFF0E7A45)
        : active
            ? const Color(0xFF215CA7)
            : const Color(0xFF74869A);
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.10),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Row(
        children: [
          Icon(
            done ? Icons.check_circle_rounded : Icons.play_circle_outline_rounded,
            color: color,
            size: 18,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              text,
              style: TextStyle(color: color, fontWeight: FontWeight.w700),
            ),
          ),
        ],
      ),
    );
  }

  Widget _heroMetric(String label, String value) {
    return Container(
      constraints: const BoxConstraints(minWidth: 160),
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: Colors.white.withValues(alpha: 0.20)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            label,
            style: const TextStyle(
              color: Color(0xFFD6E6FF),
              fontSize: 12,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 2),
          Text(
            value,
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: const TextStyle(
              color: Colors.white,
              fontWeight: FontWeight.w800,
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHeaderCard() {
    return _panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '运行反馈',
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          Text(
            '健康检查、任务提交、异常提示都会在这里集中显示。',
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(color: const Color(0xFF607388)),
          ),
          const SizedBox(height: 10),
          if (_controller.message.isEmpty && _controller.error.isEmpty)
            _hintBar(
              '等待操作，先做健康检查再提交 PDF。',
              const Color(0xFFEAF2FF),
              const Color(0xFF2B5E9E),
            ),
          if (_controller.message.isNotEmpty)
            _hintBar(
              _controller.message,
              const Color(0xFFDFF5E8),
              const Color(0xFF0F7A49),
            ),
          if (_controller.message.isNotEmpty && _controller.error.isNotEmpty)
            const SizedBox(height: 8),
          if (_controller.error.isNotEmpty)
            _hintBar(
              _controller.error,
              const Color(0xFFFBE6E6),
              const Color(0xFF9B2020),
            ),
          if (_controller.errorCode.isNotEmpty) ...[
            const SizedBox(height: 8),
            _hintBar(
              '错误码：${_controller.errorCode}',
              const Color(0xFFFFF2D8),
              const Color(0xFF8A5D10),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildControlCard() {
    final hasFile = _controller.filePath.isNotEmpty;
    final canStart = hasFile && !_controller.loading && !_controller.polling;
    return _panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '\u5ba1\u67e5\u64cd\u4f5c',
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          Text(
            '\u4e0a\u4f20\u5408\u540c\u540e\u70b9\u51fb\u5f00\u59cb\u5ba1\u67e5\uff0c\u7cfb\u7edf\u4f1a\u81ea\u52a8\u751f\u6210\u62a5\u544a\u3002',
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(color: const Color(0xFF607388)),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: Text(
                  '\u5982\u9047\u5f02\u5e38\u53ef\u70b9\u51fb\u201c\u542f\u52a8\u68c0\u67e5\u201d\u5feb\u901f\u8bca\u65ad\u3002',
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(color: const Color(0xFF607388)),
                ),
              ),
              const SizedBox(width: 8),
              OutlinedButton(
                onPressed: _controller.loading ? null : _controller.checkHealth,
                child: const Text('\u542f\u52a8\u68c0\u67e5'),
              ),
            ],
          ),
          const SizedBox(height: 12),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: const Color(0xFFF7FAFF),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: const Color(0xFFD1DEEA)),
            ),
            child: Row(
              children: [
                const Icon(Icons.picture_as_pdf_rounded,
                    color: Color(0xFFBF5B04)),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    hasFile ? _controller.fileName : '\u672a\u9009\u62e9 PDF \u6587\u4ef6',
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: 8),
                OutlinedButton(
                  onPressed: _controller.loading ? null : _controller.pickPdf,
                  child: const Text('\u9009\u62e9 PDF'),
                ),
                const SizedBox(width: 8),
                FilledButton(
                  onPressed: canStart ? _controller.startReview : null,
                  child: const Text('\u5f00\u59cb\u5ba1\u67e5'),
                ),
              ],
            ),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton(
                onPressed: (_controller.jobId != null && !_controller.loading)
                    ? _controller.refreshResult
                    : null,
                child: const Text('\u5237\u65b0\u7ed3\u679c'),
              ),
              OutlinedButton(
                onPressed: (_controller.jobId != null && !_controller.loading)
                    ? _controller.exportPdf
                    : null,
                child: const Text('\u5bfc\u51fa PDF'),
              ),
              OutlinedButton(
                onPressed: _controller.loading ? null : _controller.exportDiagnostics,
                child: const Text('\u5bfc\u51fa\u8bca\u65ad\u5305'),
              ),
              OutlinedButton(
                onPressed: _controller.loading ? null : _controller.clearAll,
                child: const Text('\u6e05\u7a7a'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _buildProgressCard() {
    final progress = _controller.progress.clamp(0, 100);
    return _panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '\u5ba1\u67e5\u8fdb\u5ea6',
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _metaPill('\u72b6\u6001', _statusToCn(_controller.status)),
              _metaPill('\u9636\u6bb5', _controller.stage.isEmpty ? '-' : _stageToCn(_controller.stage)),
              _metaPill('\u5ba1\u67e5\u7528\u65f6', _controller.reviewElapsedText),
            ],
          ),
          const SizedBox(height: 12),
          LinearProgressIndicator(
            value: progress / 100.0,
            minHeight: 10,
            borderRadius: BorderRadius.circular(999),
            color: const Color(0xFF0B6E6E),
            backgroundColor: const Color(0xFFDDE8E8),
          ),
          const SizedBox(height: 8),
          Text('\u8fdb\u5ea6\uff1a$progress%'),
          if (_controller.exportedPath.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('\u5bfc\u51fa\u6587\u4ef6\uff1a${_controller.exportedPath}',
                style: Theme.of(context).textTheme.bodySmall),
          ],
          if (_controller.diagnosticsPath.isNotEmpty) ...[
            const SizedBox(height: 8),
            Text('\u8bca\u65ad\u5305\uff1a${_controller.diagnosticsPath}',
                style: Theme.of(context).textTheme.bodySmall),
          ],
        ],
      ),
    );
  }

  Widget _buildRuntimeMetricsCard() {
    final timings = _controller.stageTimings;
    final items = timings.entries.toList();
    items.sort((a, b) => b.value.compareTo(a.value));

    var maxValue = 1.0;
    for (final item in items) {
      if (item.value > maxValue) {
        maxValue = item.value;
      }
    }

    final totalSeconds = _controller.totalSeconds;
    final llmAttempts = _controller.llmAttempts;
    final llmMaxAttempts = _controller.llmMaxAttempts;

    return _panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '运行指标',
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          Text(
            '显示阶段耗时与重试信息，用于排查慢任务。',
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(color: const Color(0xFF607388)),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _metaPill('总耗时', totalSeconds > 0 ? '${totalSeconds.toStringAsFixed(1)}s' : '--'),
              _metaPill('审查计时', _controller.reviewElapsedText),
              _metaPill('重试次数', '${_controller.llmRetryCount}'),
              _metaPill(
                'LLM尝试',
                (llmAttempts > 0 && llmMaxAttempts > 0)
                    ? '$llmAttempts/$llmMaxAttempts'
                    : '--',
              ),
            ],
          ),
          const SizedBox(height: 10),
          if (items.isEmpty)
            Text(
              '等待任务运行后，这里会显示每个阶段的耗时。',
              style: Theme.of(context)
                  .textTheme
                  .bodySmall
                  ?.copyWith(color: const Color(0xFF607388)),
            ),
          for (final item in items.take(8)) ...[
            _timingRow(
              label: _stageToCn(item.key),
              seconds: item.value,
              ratio: maxValue <= 0 ? 0 : (item.value / maxValue),
            ),
            const SizedBox(height: 8),
          ],
        ],
      ),
    );
  }

  Widget _timingRow({
    required String label,
    required double seconds,
    required double ratio,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                label,
                style: const TextStyle(
                  fontWeight: FontWeight.w700,
                  color: Color(0xFF29435E),
                ),
              ),
            ),
            Text(
              '${seconds.toStringAsFixed(2)}s',
              style: const TextStyle(
                fontWeight: FontWeight.w700,
                color: Color(0xFF51667B),
              ),
            ),
          ],
        ),
        const SizedBox(height: 5),
        LinearProgressIndicator(
          value: ratio.clamp(0, 1).toDouble(),
          minHeight: 6,
          borderRadius: BorderRadius.circular(999),
          color: const Color(0xFF2C8B8A),
          backgroundColor: const Color(0xFFDCE8EE),
        ),
      ],
    );
  }

  Widget _buildUpdateCard() {
    final hasCurrent = _controller.updateCurrentVersion.trim().isNotEmpty;
    final hasLatest = _controller.updateLatestVersion.trim().isNotEmpty;
    final canDownload = _controller.updateAvailable && !_controller.loading;
    final hasPackage = _controller.updatePackagePath.trim().isNotEmpty;

    return _panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '版本升级',
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          Text(
            '检查新版本后可直接下载安装包，不影响当前审查任务。',
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(color: const Color(0xFF607388)),
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              _metaPill('当前版本', hasCurrent ? _controller.updateCurrentVersion : '--'),
              _metaPill('最新版本', hasLatest ? _controller.updateLatestVersion : '--'),
              _metaPill('更新状态', _controller.updateAvailable ? '可升级' : '最新'),
            ],
          ),
          const SizedBox(height: 10),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              OutlinedButton(
                onPressed: _controller.loading ? null : _controller.checkUpdateManually,
                child: const Text('检查更新'),
              ),
              FilledButton(
                onPressed: canDownload ? _controller.downloadUpdatePackage : null,
                child: const Text('下载升级包'),
              ),
              OutlinedButton(
                onPressed: hasPackage ? _controller.openUpdatePackageFolder : null,
                child: const Text('打开文件夹'),
              ),
            ],
          ),
          if (_controller.updateNotes.trim().isNotEmpty) ...[
            const SizedBox(height: 10),
            _hintBar(
              '更新说明：${_controller.updateNotes}',
              const Color(0xFFEAF3FF),
              const Color(0xFF2B5E9E),
            ),
          ],
          if (hasPackage) ...[
            const SizedBox(height: 8),
            Text(
              '升级包路径：${_controller.updatePackagePath}',
              style: Theme.of(context)
                  .textTheme
                  .bodySmall
                  ?.copyWith(color: const Color(0xFF51667B)),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildResultCard() {
    final payload = _controller.reportPayload;
    if (payload == null) {
      final text = _controller.reportMarkdown.trim();
      return _panel(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              '\u5ba1\u67e5\u62a5\u544a',
              style: Theme.of(context)
                  .textTheme
                  .titleMedium
                  ?.copyWith(fontWeight: FontWeight.w800),
            ),
            const SizedBox(height: 4),
            Text(
              '\u7ed3\u679c\u6458\u8981\u4e0e\u7ed3\u6784\u5316\u8f93\u51fa',
              style: Theme.of(context)
                  .textTheme
                  .bodySmall
                  ?.copyWith(color: const Color(0xFF607388)),
            ),
            const SizedBox(height: 12),
            Text(text.isEmpty ? '\u6682\u65e0\u62a5\u544a\u5185\u5bb9\u3002' : text),
          ],
        ),
      );
    }

    final contractType = (payload['contract_type'] ?? '\u672a\u77e5').toString();
    final stampText = (payload['stamp_text'] ?? '\u672a\u68c0\u6d4b').toString();
    final confidence = (payload['confidence_text'] ?? '-').toString();
    final overview = (payload['overview'] ?? '').toString();

    return _panel(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            '\u5ba1\u67e5\u62a5\u544a',
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.w800),
          ),
          const SizedBox(height: 4),
          Text(
            '\u5408\u540c\u7c7b\u578b\u3001\u5173\u952e\u8981\u7d20\u3001\u98ce\u9669\u4e0e\u5efa\u8bae\u4e00\u4f53\u5316\u5c55\u793a',
            style: Theme.of(context)
                .textTheme
                .bodySmall
                ?.copyWith(color: const Color(0xFF607388)),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 10,
            children: [
              _metaPill('\u5408\u540c\u7c7b\u578b', contractType),
              _metaPill('\u76d6\u7ae0', stampText),
              _metaPill('\u7f6e\u4fe1\u5ea6', confidence),
            ],
          ),
          const SizedBox(height: 14),
          _subTitle('\u5ba1\u67e5\u6982\u8ff0'),
          const SizedBox(height: 6),
          Text(overview.isEmpty ? '\u6682\u65e0\u6982\u8ff0\u5185\u5bb9\u3002' : overview),
          const SizedBox(height: 14),
          _subTitle('\u5173\u952e\u8981\u7d20'),
          const SizedBox(height: 8),
          _keyFactsGrid(payload['key_facts']),
          const SizedBox(height: 14),
          _buildRiskImproveGrid(
            risks: payload['risks'],
            improvements: payload['improvements'],
          ),
        ],
      ),
    );
  }

  Widget _buildRiskImproveGrid({
    required dynamic risks,
    required dynamic improvements,
  }) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final riskCard = _sectionCard(
          title: '\u98ce\u9669\u70b9',
          icon: Icons.warning_amber_rounded,
          accent: const Color(0xFFA84444),
          child: _itemSection(risks, tone: _ItemTone.risk),
        );
        final improveCard = _sectionCard(
          title: '\u6539\u8fdb\u5efa\u8bae',
          icon: Icons.lightbulb_outline_rounded,
          accent: const Color(0xFF0E7A45),
          child: _itemSection(improvements, tone: _ItemTone.improve),
        );

        if (constraints.maxWidth >= 900) {
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: riskCard),
              const SizedBox(width: 12),
              Expanded(child: improveCard),
            ],
          );
        }

        return Column(
          children: [
            riskCard,
            const SizedBox(height: 12),
            improveCard,
          ],
        );
      },
    );
  }

  Widget _sectionCard({
    required String title,
    required IconData icon,
    required Color accent,
    required Widget child,
  }) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: accent.withValues(alpha: 0.04),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: accent.withValues(alpha: 0.25)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 18, color: accent),
              const SizedBox(width: 6),
              Text(
                title,
                style: TextStyle(
                  color: accent,
                  fontWeight: FontWeight.w800,
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          child,
        ],
      ),
    );
  }

  Widget _itemSection(dynamic raw, {required _ItemTone tone}) {
    final items = _normalizeItems(raw);
    if (items.isEmpty) {
      return Text(
        tone == _ItemTone.risk
            ? '\u672a\u8bc6\u522b\u5230\u660e\u663e\u98ce\u9669\u3002'
            : '\u6682\u65e0\u53ef\u6267\u884c\u5efa\u8bae\u3002',
        style: const TextStyle(color: Color(0xFF5C6F83)),
      );
    }
    return Column(
      children: [
        for (var i = 0; i < items.length; i++) ...[
          _itemCard(i + 1, items[i], tone: tone),
          if (i != items.length - 1) const SizedBox(height: 8),
        ],
      ],
    );
  }

  Widget _itemCard(
    int index,
    Map<String, String> item, {
    required _ItemTone tone,
  }) {
    final title = item['title'] ?? '';
    final level = item['level'] ?? '';
    final problem = item['problem'] ?? '';
    final suggestion = item['suggestion'] ?? '';

    final accent = tone == _ItemTone.risk
        ? const Color(0xFFA84444)
        : const Color(0xFF0E7A45);
    final bg = tone == _ItemTone.risk
        ? const Color(0xFFFFF7F7)
        : const Color(0xFFF4FBF7);
    final resolvedTitle = title.isEmpty ? '\u672a\u547d\u540d\u6761\u76ee' : title;

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: accent.withValues(alpha: 0.22)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            level.isEmpty
                ? '$index. $resolvedTitle'
                : '$index. $resolvedTitle ($level)',
            style: TextStyle(
              fontWeight: FontWeight.w700,
              color: accent,
            ),
          ),
          if (problem.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text('\u95ee\u9898\uff1a$problem', style: const TextStyle(height: 1.35)),
          ],
          if (suggestion.isNotEmpty) ...[
            const SizedBox(height: 5),
            Text('\u5efa\u8bae\uff1a$suggestion', style: const TextStyle(height: 1.35)),
          ],
        ],
      ),
    );
  }

  Widget _keyFactsGrid(dynamic raw) {
    if (raw is! Map) {
      return const Text('未提取到关键要素。');
    }
    final entries = raw.entries.toList();
    if (entries.isEmpty) {
      return const Text('未提取到关键要素。');
    }
    return Wrap(
      spacing: 10,
      runSpacing: 10,
      children: [
        for (final entry in entries)
          Container(
            width: 260,
            padding: const EdgeInsets.all(10),
            decoration: BoxDecoration(
              color: const Color(0xFFFFFCF8),
              borderRadius: BorderRadius.circular(10),
              border: Border.all(color: const Color(0xFFF0DEC7)),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(entry.key.toString(),
                    style: const TextStyle(fontWeight: FontWeight.w700)),
                const SizedBox(height: 4),
                Text(entry.value.toString()),
              ],
            ),
          ),
      ],
    );
  }

  Widget _subTitle(String text) {
    return Text(
      text,
      style: Theme.of(context).textTheme.titleSmall?.copyWith(
            fontWeight: FontWeight.w700,
            color: const Color(0xFF1D3535),
          ),
    );
  }

  String _statusToCn(String status) {
    return switch (status) {
      'idle' => '待命',
      'submitting' => '提交中',
      'running' => '处理中',
      'done' => '已完成',
      'error' => '失败',
      _ => status,
    };
  }

  int _workflowIndex() {
    if (_controller.status == 'done') {
      return 3;
    }
    if (_controller.status == 'running') {
      return 2;
    }
    if (_controller.status == 'submitting' || _controller.jobId != null) {
      return 1;
    }
    return 0;
  }

  String _stageToCn(String stage) {
    final s = stage.toLowerCase();
    if (s.contains('submit')) {
      return '提交任务';
    }
    if (s.contains('stamp')) {
      return '盖章识别';
    }
    if (s.contains('ocr') || s.contains('mineru')) {
      return '文本抽取';
    }
    if (s.contains('llm')) {
      return '智能审查';
    }
    if (s.contains('done')) {
      return '处理完成';
    }
    if (s == 'start') {
      return '准备中';
    }
    return stage;
  }

  Widget _metaPill(String label, String value) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
      decoration: BoxDecoration(
        color: const Color(0xFFF2F6FA),
        borderRadius: BorderRadius.circular(999),
        border: Border.all(color: const Color(0xFFD7E1EA)),
      ),
      child: Text('$label: $value',
          style: const TextStyle(fontSize: 12.5, fontWeight: FontWeight.w600)),
    );
  }

  Widget _hintBar(String text, Color bg, Color fg) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: fg.withValues(alpha: 0.25)),
      ),
      child: Text(
        text,
        style: TextStyle(color: fg, fontWeight: FontWeight.w600),
      ),
    );
  }

  Widget _panel({required Widget child}) {
    return Container(
      width: double.infinity,
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
        color: Colors.white.withValues(alpha: 0.95),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(color: const Color(0xFFD1DDEA)),
        boxShadow: const [
          BoxShadow(
            color: Color(0x140A2548),
            blurRadius: 16,
            offset: Offset(0, 7),
          ),
        ],
      ),
      child: child,
    );
  }

  List<Map<String, String>> _normalizeItems(dynamic raw) {
    if (raw is! List) {
      return const [];
    }
    final out = <Map<String, String>>[];
    for (final item in raw) {
      if (item is String) {
        final text = item.trim();
        if (text.isEmpty) {
          continue;
        }
        out.add({
          'title': text,
          'problem': '',
          'suggestion': '',
          'level': '',
        });
        continue;
      }
      if (item is Map) {
        out.add({
          'title': (item['title'] ?? item['name'] ?? item['item'] ?? '').toString(),
          'problem':
              (item['problem'] ?? item['issue'] ?? item['description'] ?? '')
                  .toString(),
          'suggestion': (item['suggestion'] ?? item['advice'] ?? '').toString(),
          'level': (item['level'] ?? item['severity'] ?? '').toString(),
        });
      }
    }
    return out;
  }


}







