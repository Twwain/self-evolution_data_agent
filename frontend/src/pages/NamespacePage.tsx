/* ════════════════════════════════════════════
 *  命名空间管理页 — 顶部下拉选择 + 详情面板 (Tab: 数据源/Git)
 *
 *  设计: 与 KnowledgePage 对齐, 避免左右分栏导致页面过宽;
 *  初次进入自动恢复 localStorage 记忆的命名空间 (NamespaceSelector 内).
 * ════════════════════════════════════════════ */

import React, { useCallback, useEffect, useState } from "react";
import {
  Button,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tabs,
  Tag,
  message,
} from "antd";
import {
  PlusOutlined,
  DeleteOutlined,
} from "@ant-design/icons";
import * as api from "@/api";
import NamespaceSelector from "@/components/NamespaceSelector";
import type {
  BatchStatus,
  DataSource,
  DbType,
  GitRepo,
  Namespace,
} from "@/types";
import { DB_TYPE_META } from "@/types";

import RepoManager from "@/components/RepoManager";
import { clearLastNamespaceId } from "@/hooks/useLastNamespaceId";
import styles from "@/styles/namespace.module.css";
import globalStyles from "@/styles/global.module.css";

const NamespacePage: React.FC = () => {
  const [activeNs, setActiveNs] = useState<Namespace | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [showDsModal, setShowDsModal] = useState(false);
  const [form] = Form.useForm();
  const [dsForm] = Form.useForm();
  /** 监听数据源表单 db_type 变化, 用于动态调整 label/placeholder */
  const dsDbType = Form.useWatch<string>("db_type", dsForm);
  /** 重挂 NamespaceSelector, 让 create/delete 后重新拉列表 + 重新默认 */
  const [selectorKey, setSelectorKey] = useState(0);

  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [repos, setRepos] = useState<GitRepo[]>([]);
  const [batchStatus, setBatchStatus] = useState<BatchStatus | null>(null);
  const [refreshingDs, setRefreshingDs] = useState<number | null>(null);

  const loadDetail = useCallback(async (ns: Namespace) => {
    const [ds, repoRes] = await Promise.all([
      api.fetchDataSources(ns.id),
      api.fetchRepos(ns.id),
    ]);
    setDatasources(ds);
    setRepos(repoRes.repos);
    setBatchStatus(repoRes.batch_status);
  }, []);

  useEffect(() => {
    if (activeNs) loadDetail(activeNs);
  }, [activeNs, loadDetail]);

  const handleCreate = async () => {
    const vals = await form.validateFields();
    await api.createNamespace(vals);
    message.success("创建成功");
    setShowCreate(false);
    form.resetFields();
    setSelectorKey((k) => k + 1);
  };

  const handleDelete = async (id: number) => {
    await api.deleteNamespace(id);
    message.success("已删除");
    if (activeNs?.id === id) {
      setActiveNs(null);
      clearLastNamespaceId();
    }
    setSelectorKey((k) => k + 1);
  };

  const handleAddDs = async () => {
    if (!activeNs) return;
    const vals = await dsForm.validateFields();
    try {
      await api.addDataSource(activeNs.id, vals);
      message.success("数据源添加成功, 连接已验证");
      setShowDsModal(false);
      dsForm.resetFields();
      loadDetail(activeNs);
    } catch (e: any) {
      // 后端连不上返回 400, Modal 不关, 展示具体原因
      const detail = e?.response?.data?.detail || "连接失败, 请检查连接信息";
      message.error(`数据源添加失败: ${detail}`);
    }
  };

  const handleRefreshSchema = async (dsId: number) => {
    if (!activeNs) return;
    setRefreshingDs(dsId);
    try {
      const result = await api.refreshSchema(activeNs.id, dsId);
      message.success(result.message);
    } catch {
      message.error("Schema 刷新请求失败");
    } finally {
      setRefreshingDs(null);
    }
  };

  const handleDeleteDs = async (dsId: number) => {
    if (!activeNs) return;
    await api.deleteDataSource(activeNs.id, dsId);
    message.success("数据源已删除");
    loadDetail(activeNs);
  };

  const reloadRepos = useCallback(async () => {
    if (!activeNs) return;
    const res = await api.fetchRepos(activeNs.id);
    setRepos(res.repos);
    setBatchStatus(res.batch_status);
  }, [activeNs]);

  return (
    <div>
      <div className={globalStyles.pageHeader}>
        <div>
          <h1 className={globalStyles.pageTitle}>命名空间</h1>
          <p className={globalStyles.pageSubtitle}>
            管理数据源和 Git 仓库 (查询规则在知识库管理)
          </p>
        </div>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setShowCreate(true)}
        >
          新建空间
        </Button>
      </div>

      <div className={styles.container}>
        <div className={styles.toolbar}>
          <NamespaceSelector
            key={selectorKey}
            value={activeNs?.id}
            onChange={(_id, ns) => setActiveNs(ns)}
          />
          {activeNs && (
            <>
              <span className={styles.detailMeta}>
                {activeNs.slug} · 创建于 {activeNs.created_at?.slice(0, 10)}
              </span>
              <Popconfirm
                title="确认删除?"
                onConfirm={() => handleDelete(activeNs.id)}
              >
                <Button size="small" danger>
                  删除当前空间
                </Button>
              </Popconfirm>
            </>
          )}
        </div>

        {activeNs ? (
          <div className={styles.detailPanel}>
            <Tabs
              className={styles.detailTabs}
              items={[
                {
                  key: "ds",
                  label: "数据源",
                  children: (
                    <div className={styles.detailContent}>
                      <Button
                        size="small"
                        type="default"
                        icon={<PlusOutlined />}
                        onClick={() => setShowDsModal(true)}
                        style={{
                          marginBottom: 12,
                          background: "#eff6ff",
                          borderColor: "#dbeafe",
                          color: "#2563eb",
                        }}
                      >
                        添加数据源
                      </Button>
                      {datasources.map((ds) => {
                        const profiledAt = ds.db_profile?.profiled_at as string | undefined;
                        const version = ds.db_profile?.version as string | undefined;
                        const objCount = ds.db_profile?.object_count as number | undefined;
                        return (
                        <div key={ds.id} className={styles.dsCard} data-testid="ds-card">
                          <div className={styles.dsInfo}>
                            <div className={styles.dsIcon}>
                              {(DB_TYPE_META[ds.db_type] ?? DB_TYPE_META.mysql).short}
                            </div>
                            <div>
                              <div className={styles.dsName}>{ds.database}</div>
                              <div className={styles.dsMeta}>
                                {ds.host}:{ds.port} · {ds.db_type.toUpperCase()}
                                {version ? ` · v${version}` : ""}
                                {typeof objCount === "number" ? ` · ${objCount} 对象` : ""}
                              </div>
                              {ds.description ? (
                                <div className={styles.dsMeta}>{ds.description}</div>
                              ) : null}
                            </div>
                          </div>
                          <div className={styles.dsActions}>
                            <Tag color={profiledAt ? "success" : "default"}>
                              {profiledAt
                                ? `初始连接于 ${profiledAt.slice(0, 16).replace("T", " ")}`
                                : "已添加"}
                            </Tag>
                            {(DB_TYPE_META[ds.db_type] ?? DB_TYPE_META.mysql).isSql && (
                              <Button
                                size="small"
                                loading={refreshingDs === ds.id}
                                onClick={() => handleRefreshSchema(ds.id)}
                              >
                                刷新 Schema
                              </Button>
                            )}
                            <Popconfirm
                              title="确认删除数据源?"
                              description="删除后相关知识条目将失效"
                              onConfirm={() => handleDeleteDs(ds.id)}
                            >
                              <Button size="small" danger icon={<DeleteOutlined />}>
                                删除
                              </Button>
                            </Popconfirm>
                          </div>
                        </div>
                        );
                      })}
                    </div>
                  ),
                },
                {
                  key: "repos",
                  label: "Git 仓库",
                  children: (
                    <RepoManager
                      nsId={activeNs.id}
                      datasources={datasources}
                      repos={repos}
                      batchStatus={batchStatus}
                      onReposChange={reloadRepos}
                    />
                  ),
                },
              ]}
            />
          </div>
        ) : (
          <div className={styles.empty}>
            暂无命名空间, 请先新建
          </div>
        )}
      </div>

      <Modal
        title="创建命名空间"
        open={showCreate}
        onOk={handleCreate}
        onCancel={() => setShowCreate(false)}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="slug"
            label="标识 (英文)"
            rules={[{ required: true, pattern: /^[a-z0-9_-]+$/ }]}
          >
            <Input placeholder="如: my-namespace" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`添加数据源 — ${activeNs?.name}`}
        open={showDsModal}
        onOk={handleAddDs}
        onCancel={() => setShowDsModal(false)}
      >
        <Form
          form={dsForm}
          layout="vertical"
          onValuesChange={(changed) => {
            // 选择数据库类型时自动填写默认端口
            if (changed.db_type) {
              const meta = DB_TYPE_META[changed.db_type as DbType];
              if (meta) dsForm.setFieldValue("port", meta.defaultPort);
            }
          }}
        >
          <Form.Item name="db_type" label="类型" rules={[{ required: true }]}>
            <Select
              options={Object.entries(DB_TYPE_META).map(([v, m]) => ({
                value: v, label: m.label,
              }))}
            />
          </Form.Item>
          <Form.Item name="host" label="主机" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="port" label="端口" rules={[{ required: true }]}>
            <InputNumber style={{ width: "100%" }} />
          </Form.Item>
          {/* Oracle 的 database 字段含义是 Service Name */}
          <Form.Item
            name="database"
            label={
              dsDbType === "oracle" ? "Service Name" : "数据库"
            }
            tooltip={
              dsDbType === "oracle"
                ? "Oracle Service Name, 例如 orclpdb"
                : undefined
            }
            rules={[{ required: true }]}
          >
            <Input
              placeholder={
                dsDbType === "oracle" ? "orclpdb" : undefined
              }
            />
          </Form.Item>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item
            name="description"
            label="用途描述"
            tooltip="这个库存什么数据, 便于 AI 理解 (选填)"
          >
            <Input.TextArea
              rows={2}
              placeholder="例: 订单交易库 / 设备运维数据"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default NamespacePage;
