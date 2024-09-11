package com.stephenshen.arkquant.repository;

import com.stephenshen.arkquant.entity.Security;

import java.util.Collection;
import java.util.List;
import java.util.Map;

/**
 * 标的数据仓库
 *
 * @author stephenshen
 * @date 2024/9/1 16:36:04
 */
public interface SecurityRepository {

    /**
     * 获取标的信息
     * @param symbol
     * @return
     */
    Security getSecurity(String symbol);

    /**
     * 获取标的列表
     * @param symbols
     * @return
     */
    List<Security> getSecurities(Collection<String> symbols);

    /**
     * 获取标的映射
     * @param symbols
     * @return 标的代码 -> 标的信息
     */
    Map<String, Security> getSecurityMap(Collection<String> symbols);
}